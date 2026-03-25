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
    private static final int MAX_ATTEMPTS = 5;
    private static final int MAX_THROTTLE_RETRIES = 4;
    private static final long[] THROTTLE_BACKOFF_MS = {2_000, 4_000, 8_000, 15_000};
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
            int throttleRetry = 0;
            boolean success = false;
            boolean retryMsgSent = false;

            while (attempt < MAX_ATTEMPTS && !Thread.currentThread().isInterrupted()) {
                attempt++;
                AtomicBoolean hadToolCalls = new AtomicBoolean(false);
                AtomicInteger stepCount = new AtomicInteger(0);
                StringBuilder textAccumulator = new StringBuilder();
                CountDownLatch latch = new CountDownLatch(1);
                AtomicBoolean hasError = new AtomicBoolean(false);
                var errorMsg = new java.util.concurrent.atomic.AtomicReference<String>();

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
                                errorMsg.set(err.getMessage() != null ? err.getMessage() : err.toString());
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

                if (hasError.get() && isRetriableError(errorMsg.get())) {
                    if (throttleRetry < MAX_THROTTLE_RETRIES) {
                        long waitMs = THROTTLE_BACKOFF_MS[Math.min(throttleRetry, THROTTLE_BACKOFF_MS.length - 1)];
                        throttleRetry++;
                        attempt--;
                        log.warn("Retriable API error, retry {} after {}ms: {}", throttleRetry, waitMs,
                                errorMsg.get() != null ? errorMsg.get().substring(0, Math.min(80, errorMsg.get().length())) : "null");
                        if (!retryMsgSent) {
                            sendEvent(sessionId, Map.of("type", "text", "content",
                                    "\n\n⏳ API 连接异常，自动重试中...\n"));
                            retryMsgSent = true;
                        }
                        try {
                            Thread.sleep(waitMs);
                        } catch (InterruptedException ie) {
                            Thread.currentThread().interrupt();
                            break;
                        }
                        lastUserMsg = "[SYSTEM] 之前的API调用因连接问题中断。请调用 browser_observe 获取当前页面状态,然后继续执行任务。";
                        continue;
                    }
                    sendEvent(sessionId, Map.of("type", "error", "message",
                            "API 连接持续异常，请稍后再试或更换模型。"));
                    sendEvent(sessionId, Map.of("type", "done"));
                    success = true;
                    break;
                }

                if (hasError.get()) {
                    sendEvent(sessionId, Map.of("type", "error", "message", errorMsg.get()));
                    sendEvent(sessionId, Map.of("type", "done"));
                    success = true;
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

                if (hadToolCalls.get()) {
                    sendEvent(sessionId, Map.of("type", "done"));
                    success = true;
                    break;
                }

                boolean hallucinating = isHallucinatingText(text);

                if (attempt < MAX_ATTEMPTS) {
                    log.warn("Agent {} (attempt {}). Text length: {}",
                            hallucinating ? "hallucinated" : "no tool calls", attempt, text.length());
                    if (!retryMsgSent) {
                        sendEvent(sessionId, Map.of("type", "text", "content",
                                "\n\n🔄 Agent 未执行操作，自动重试中...\n"));
                        retryMsgSent = true;
                    }
                    lastUserMsg = hallucinating
                            ? "[SYSTEM] 你刚才产生了幻觉:在文字中描述了操作(如'已点击','已输入','让我观察'),但实际上没有调用任何工具。浏览器没有执行任何操作。现在请立刻调用 browser_observe 获取浏览器的真实状态,然后用工具执行操作。只说一句话,然后调用工具。"
                            : "[SYSTEM] 你刚才没有调用任何工具。请立刻调用 browser_observe 观察当前页面状态,然后根据结果执行下一步操作。回复要简短,必须包含工具调用。";
                }
            }

            if (!success) {
                if (!retryMsgSent) {
                    sendEvent(sessionId, Map.of("type", "text", "content",
                            "\n\n⚠️ Agent 未能执行操作，请尝试更明确的指令。"));
                }
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

    private static final List<String> HALLUCINATION_PATTERNS = List.of(
            "已输入", "已点击", "已提交", "密码已", "账号已", "账号密码已",
            "让我观察", "让我点击", "让我再次", "让我尝试", "让我重新",
            "登录按钮点击后", "登录按钮已", "观察登录结果", "观察结果",
            "页面仍然", "登录框仍", "登录似乎", "登录可能",
            "刷新页面", "重新输入", "再次尝试", "这次确保",
            "登录成功", "搜索到了", "看到了", "进入了"
    );

    private static boolean isHallucinatingText(String text) {
        if (text == null || text.length() < 20) return false;

        int fakeToolLines = 0;
        for (String line : text.split("\n")) {
            String trimmed = line.trim();
            if (trimmed.startsWith("[tools:") || trimmed.startsWith("[tool:") ||
                    trimmed.startsWith("browser_") && trimmed.contains("=>")) {
                fakeToolLines++;
            }
        }
        if (fakeToolLines >= 2) return true;

        int matches = 0;
        for (String pattern : HALLUCINATION_PATTERNS) {
            if (text.contains(pattern)) matches++;
        }
        return matches >= 2 || (text.length() > 150 && !text.contains("browser_observe"));
    }

    private static boolean isRetriableError(String msg) {
        if (msg == null) return false;
        String lower = msg.toLowerCase();
        return lower.contains("throttl") || lower.contains("too many request")
                || lower.contains("rate limit") || lower.contains("429")
                || (lower.contains("503") && lower.contains("capacity"))
                || lower.contains("status code must not be negative or zero")
                || lower.contains("invalid status line")
                || lower.contains("protocolexception")
                || lower.contains("connection reset")
                || lower.contains("connection closed")
                || lower.contains("broken pipe")
                || lower.contains("stream closed")
                || lower.contains("unexpected end of stream")
                || lower.contains("operation timed out")
                || lower.contains("unknown error")
                || lower.contains("api_error")
                || lower.contains("internal_error")
                || lower.contains("server_error")
                || lower.contains("502") || lower.contains("500");
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
