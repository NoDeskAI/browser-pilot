package com.nodeskai.agent.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Parses stray [TOOL_CALL]...[/TOOL_CALL] text blocks that some models emit
 * instead of using proper tool calling. Supports multiple field name conventions.
 */
public class StrayToolCallParser {

    private static final Logger log = LoggerFactory.getLogger(StrayToolCallParser.class);
    private static final Pattern BLOCK_PATTERN = Pattern.compile("\\[TOOL_CALL](.*?)\\[/TOOL_CALL]", Pattern.DOTALL);
    private static final Pattern OBJ_PATTERN = Pattern.compile("\\{.*}", Pattern.DOTALL);
    private static final Set<String> TOOL_NAME_FIELDS = Set.of("tool", "name", "function");
    private static final Set<String> ARGS_FIELDS = Set.of("args", "parameters", "params", "input");

    public record ParsedToolCall(String toolName, Map<String, Object> args) {}

    public static ParsedToolCall parse(String text, Set<String> knownTools) {
        Matcher blockMatch = BLOCK_PATTERN.matcher(text);
        if (!blockMatch.find()) return null;
        String block = blockMatch.group(1);

        Matcher objMatch = OBJ_PATTERN.matcher(block);
        if (objMatch.find()) {
            try {
                String raw = objMatch.group();
                // Normalize common malformations
                raw = raw.replace("'", "\"");
                raw = raw.replace("=>", ":");
                raw = raw.replaceAll("\"(\\w+)\\s*:\\s*\\{", "\"$1\": {");
                raw = raw.replaceAll("([,{]\\s*)([A-Za-z_]\\w*)\\s*:", "$1\"$2\":");

                ObjectMapper mapper = new ObjectMapper();
                JsonNode obj = mapper.readTree(raw);

                String toolName = null;
                for (String f : TOOL_NAME_FIELDS) {
                    if (obj.has(f) && obj.get(f).isTextual()) {
                        toolName = obj.get(f).asText();
                        break;
                    }
                }
                if (toolName == null || !knownTools.contains(toolName)) return null;

                JsonNode argsNode = null;
                for (String f : ARGS_FIELDS) {
                    if (obj.has(f) && obj.get(f).isObject()) {
                        argsNode = obj.get(f);
                        break;
                    }
                }

                Map<String, Object> args;
                if (argsNode != null) {
                    args = mapper.convertValue(argsNode, Map.class);
                } else {
                    args = new LinkedHashMap<>(mapper.convertValue(obj, Map.class));
                    TOOL_NAME_FIELDS.forEach(args::remove);
                }
                return new ParsedToolCall(toolName, args);
            } catch (Exception e) {
                log.debug("Failed to parse stray tool call JSON: {}", e.getMessage());
            }
        }

        // Fallback regex
        Pattern toolMatch = Pattern.compile("[\"']?(?:tool|name|function)[\"']?\\s*(?:=>|:)\\s*[\"']([^\"']+)[\"']");
        Matcher tm = toolMatch.matcher(block);
        if (!tm.find()) return null;
        String toolName = tm.group(1);
        if (!knownTools.contains(toolName)) return null;

        Map<String, Object> args = new LinkedHashMap<>();
        Pattern kvPattern = Pattern.compile("[\"']?(\\w+)[\"']?\\s*(?::|=>)\\s*(?:\"([^\"]*)\"|'([^']*)'|(true|false|\\d+(?:\\.\\d+)?))");
        Matcher kv = kvPattern.matcher(block);
        while (kv.find()) {
            String key = kv.group(1);
            if (TOOL_NAME_FIELDS.contains(key)) continue;
            if (kv.group(2) != null) args.put(key, kv.group(2));
            else if (kv.group(3) != null) args.put(key, kv.group(3));
            else if ("true".equals(kv.group(4))) args.put(key, true);
            else if ("false".equals(kv.group(4))) args.put(key, false);
            else args.put(key, kv.group(4).contains(".") ? Double.parseDouble(kv.group(4)) : Integer.parseInt(kv.group(4)));
        }

        return new ParsedToolCall(toolName, args);
    }
}
