package com.nodeskai.agent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.nodeskai.agent.factory.LlmFactory;
import com.nodeskai.agent.model.ChatRequest;
import com.nodeskai.agent.tool.BrowserTools;
import dev.langchain4j.data.message.*;
import dev.langchain4j.model.chat.StreamingChatModel;
import dev.langchain4j.service.AiServices;
import dev.langchain4j.service.TokenStream;
import dev.langchain4j.memory.chat.MessageWindowChatMemory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Service;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;

import jakarta.annotation.PostConstruct;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

@Service
public class AgentService {

    private static final Logger log = LoggerFactory.getLogger(AgentService.class);
    private static final int MAX_ATTEMPTS = 3;
    private static final int MAX_STEPS = 25;
    private static final Set<String> KNOWN_TOOLS = Set.of(
            "browser_navigate", "browser_list_tabs", "browser_switch_tab",
            "browser_observe", "browser_click", "browser_click_text",
            "browser_click_element", "browser_triple_like", "browser_type",
            "browser_key", "browser_scroll", "browser_get_page_info"
    );

    private final LlmFactory llmFactory;
    private final BrowserTools browserTools;
    private final ObjectMapper mapper;

    private String systemPrompt;

    private final ConcurrentHashMap<String, WebSocketSession> sessions = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, Future<?>> runningAgents = new ConcurrentHashMap<>();

    public AgentService(LlmFactory llmFactory, BrowserTools browserTools, ObjectMapper mapper) {
        this.llmFactory = llmFactory;
        this.browserTools = browserTools;
        this.mapper = mapper;
    }

    @PostConstruct
    void init() throws IOException {
        systemPrompt = new ClassPathResource("system-prompt.txt")
                .getContentAsString(StandardCharsets.UTF_8);
    }

    // ------------------------------------------------------------------
    // Session registry
    // ------------------------------------------------------------------

    public void registerSession(String sessionId, WebSocketSession session) {
        sessions.put(sessionId, session);
    }

    public void removeSession(String sessionId) {
        abort(sessionId);
        sessions.remove(sessionId);
    }

    public void abort(String sessionId) {
        Future<?> f = runningAgents.remove(sessionId);
        if (f != null) {
            f.cancel(true);
            log.info("Agent aborted for session {}", sessionId);
        }
    }

    public void registerRunning(String sessionId, Future<?> future) {
        Future<?> old = runningAgents.put(sessionId, future);
        if (old != null) old.cancel(true);
    }

    // ------------------------------------------------------------------
    // Core agent loop
    // ------------------------------------------------------------------

    public void runAgent(ChatRequest request, String sessionId) {
        try {
            if (request.apiKey() == null || request.apiKey().isBlank()) {
                sendEvent(sessionId, Map.of("type", "error", "message", "请先配置 API Key"));
                sendEvent(sessionId, Map.of("type", "done"));
                return;
            }

            StreamingChatModel model = llmFactory.create(request);

            // Build chat memory from request messages
            var memory = MessageWindowChatMemory.withMaxMessages(50);
            if (request.messages() != null) {
                for (Map<String, String> m : request.messages()) {
                    String role = m.getOrDefault("role", "user");
                    String content = m.getOrDefault("content", "");
                    if ("user".equals(role)) {
                        memory.add(new UserMessage(content));
                    } else if ("assistant".equals(role)) {
                        memory.add(new AiMessage(content));
                    }
                }
            }

            log.info("Agent start, model={}, provider={}", request.model(), request.apiType());

            interface AgentInterface {
                TokenStream chat(String message);
            }

            var agent = AiServices.builder(AgentInterface.class)
                    .streamingChatModel(model)
                    .chatMemory(memory)
                    .tools(browserTools)
                    .systemMessageProvider(chatMemoryId -> systemPrompt)
                    .build();

            // The last user message drives the agent
            String lastUserMsg = "";
            if (request.messages() != null) {
                for (int i = request.messages().size() - 1; i >= 0; i--) {
                    if ("user".equals(request.messages().get(i).get("role"))) {
                        lastUserMsg = request.messages().get(i).getOrDefault("content", "");
                        break;
                    }
                }
            }

            int attempt = 0;
            boolean success = false;

            while (attempt < MAX_ATTEMPTS && !Thread.currentThread().isInterrupted()) {
                attempt++;
                AtomicBoolean hadToolCalls = new AtomicBoolean(false);
                AtomicInteger stepCount = new AtomicInteger(0);
                StringBuilder textAccumulator = new StringBuilder();
                CountDownLatch latch = new CountDownLatch(1);
                AtomicBoolean hasError = new AtomicBoolean(false);

                String msgToSend = lastUserMsg;

                try {
                    agent.chat(msgToSend)
                            .onPartialResponse(token -> {
                                textAccumulator.append(token);
                                sendEvent(sessionId, Map.of("type", "text", "content", token));
                            })
                            .beforeToolExecution(before -> {
                                hadToolCalls.set(true);
                                int step = stepCount.incrementAndGet();
                                if (step > MAX_STEPS) {
                                    throw new RuntimeException("Step limit exceeded (" + MAX_STEPS + ")");
                                }
                                var req = before.request();
                                log.info("tool-call: {}({})", req.name(),
                                        req.arguments() != null ? req.arguments().substring(0, Math.min(100, req.arguments().length())) : "");
                                sendEvent(sessionId, toolCallEvent(req));
                            })
                            .onToolExecuted(exec -> {
                                var req = exec.request();
                                String resultStr = exec.result();
                                log.info("tool-result: {} -> {}", req.name(),
                                        resultStr != null ? resultStr.substring(0, Math.min(100, resultStr.length())) : "null");
                                Object resultObj = parseJsonSafe(resultStr);
                                sendEvent(sessionId, toolResultEvent(req, resultObj));
                            })
                            .onCompleteResponse(resp -> latch.countDown())
                            .onError(err -> {
                                log.error("Agent stream error: {}", err.getMessage());
                                hasError.set(true);
                                sendEvent(sessionId, Map.of("type", "error", "message", err.getMessage()));
                                latch.countDown();
                            })
                            .start();

                    latch.await();
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    break;
                } catch (Exception e) {
                    log.error("Agent execution error: {}", e.getMessage());
                    sendEvent(sessionId, Map.of("type", "error", "message", e.getMessage()));
                    break;
                }

                // Handle stray [TOOL_CALL] text
                String text = textAccumulator.toString();
                if (text.contains("[TOOL_CALL]")) {
                    var parsed = StrayToolCallParser.parse(text, KNOWN_TOOLS);
                    if (parsed != null) {
                        log.info("Auto-executing stray tool call: {}", parsed.toolName());
                        sendEvent(sessionId, Map.of("type", "text", "content",
                                "\n\n\uD83D\uDD27 检测到文本格式的工具调用，自动执行中..."));
                        hadToolCalls.set(true);
                    } else {
                        sendEvent(sessionId, Map.of("type", "text", "content",
                                "\n\n⚠️ 模型未正确调用工具。请重试，或换一个支持 tool calling 的模型（推荐 Claude / GPT-4o）。"));
                    }
                }

                if (hadToolCalls.get() || hasError.get()) {
                    sendEvent(sessionId, Map.of("type", "done"));
                    success = true;
                    break;
                }

                if (attempt < MAX_ATTEMPTS) {
                    log.warn("Agent finished without tool calls (attempt {}). Retrying...", attempt);
                    sendEvent(sessionId, Map.of("type", "text", "content",
                            "\n\n\uD83D\uDD04 Agent 未执行操作，自动重试中...\n"));
                    String retryHint = text.contains("验证码")
                            ? "你刚才描述了验证码操作但没有调用工具。请立刻调用 browser_observe 观察当前页面，然后执行具体操作。"
                            : "你刚才没有调用任何工具。请立刻调用 browser_observe 观察当前页面状态，然后根据结果执行下一步操作。";
                    lastUserMsg = retryHint;
                }
            }

            if (!success) {
                sendEvent(sessionId, Map.of("type", "text", "content",
                        "\n\n💡 Agent 连续未执行操作。请尝试更明确的指令，或清除对话重新开始。"));
                sendEvent(sessionId, Map.of("type", "done"));
            }

        } catch (Exception e) {
            log.error("Fatal error in agent: {}", e.getMessage(), e);
            sendEvent(sessionId, Map.of("type", "error", "message", e.getMessage()));
            sendEvent(sessionId, Map.of("type", "done"));
        }
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    public void sendEvent(String sessionId, Map<String, Object> event) {
        WebSocketSession session = sessions.get(sessionId);
        if (session == null || !session.isOpen()) return;
        try {
            session.sendMessage(new TextMessage(mapper.writeValueAsString(event)));
        } catch (IOException e) {
            log.warn("Failed to send WS event: {}", e.getMessage());
        }
    }

    private Map<String, Object> toolCallEvent(dev.langchain4j.agent.tool.ToolExecutionRequest req) {
        Map<String, Object> evt = new LinkedHashMap<>();
        evt.put("type", "tool_call");
        evt.put("id", req.id());
        evt.put("name", req.name());
        evt.put("args", parseJsonSafe(req.arguments()));
        return evt;
    }

    private Map<String, Object> toolResultEvent(dev.langchain4j.agent.tool.ToolExecutionRequest req, Object resultObj) {
        Map<String, Object> evt = new LinkedHashMap<>();
        evt.put("type", "tool_result");
        evt.put("id", req.id());
        evt.put("name", req.name());
        evt.put("result", trimResultForFrontend(req.name(), resultObj));
        return evt;
    }

    @SuppressWarnings("unchecked")
    private Object trimResultForFrontend(String toolName, Object result) {
        if (!(result instanceof Map)) return result;
        Map<String, Object> map = (Map<String, Object>) result;
        if ("browser_observe".equals(toolName)) {
            Map<String, Object> slim = new LinkedHashMap<>();
            slim.put("ok", map.getOrDefault("ok", true));
            slim.put("url", map.getOrDefault("url", ""));
            slim.put("title", map.getOrDefault("title", ""));
            slim.put("elementCount", map.getOrDefault("elementCount", 0));
            return slim;
        }
        Object cp = map.get("currentPage");
        if (cp instanceof Map) {
            Map<String, Object> cpMap = (Map<String, Object>) cp;
            Map<String, Object> trimmed = new LinkedHashMap<>(map);
            Map<String, Object> slimPage = new LinkedHashMap<>();
            slimPage.put("url", cpMap.getOrDefault("url", ""));
            slimPage.put("title", cpMap.getOrDefault("title", ""));
            slimPage.put("elementCount", cpMap.getOrDefault("elementCount", 0));
            trimmed.put("currentPage", slimPage);
            return trimmed;
        }
        return result;
    }

    private Object parseJsonSafe(String json) {
        if (json == null || json.isBlank()) return Map.of();
        try {
            return mapper.readValue(json, Map.class);
        } catch (Exception e) {
            return json;
        }
    }

}
