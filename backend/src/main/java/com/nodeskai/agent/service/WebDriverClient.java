package com.nodeskai.agent.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.nodeskai.agent.script.JsScripts;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.locks.ReentrantLock;

@Service
public class WebDriverClient {

    private static final Logger log = LoggerFactory.getLogger(WebDriverClient.class);

    private final String seleniumBase;
    private final ObjectMapper mapper;
    private final JsScripts jsScripts;
    private final HttpClient httpClient;

    private volatile String sessionId;
    private final ReentrantLock sessionLock = new ReentrantLock();

    public static final Map<String, String> KEY_MAP = Map.ofEntries(
            Map.entry("Enter", "\uE007"), Map.entry("Tab", "\uE004"),
            Map.entry("Escape", "\uE00C"), Map.entry("Backspace", "\uE003"),
            Map.entry("Delete", "\uE017"), Map.entry("Space", "\uE00D"),
            Map.entry("ArrowUp", "\uE013"), Map.entry("ArrowDown", "\uE014"),
            Map.entry("ArrowLeft", "\uE012"), Map.entry("ArrowRight", "\uE011"),
            Map.entry("Home", "\uE011"), Map.entry("End", "\uE010"),
            Map.entry("PageUp", "\uE00E"), Map.entry("PageDown", "\uE00F")
    );

    public WebDriverClient(
            @Value("${app.selenium-base-url}") String seleniumBase,
            ObjectMapper mapper,
            JsScripts jsScripts
    ) {
        this.seleniumBase = seleniumBase.replaceAll("/+$", "");
        this.mapper = mapper;
        this.jsScripts = jsScripts;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .build();
    }

    // ------------------------------------------------------------------
    // Low-level WebDriver HTTP
    // ------------------------------------------------------------------

    public JsonNode wdFetch(String path, String method, Object body, Duration timeout)
            throws IOException, InterruptedException {
        var reqBuilder = HttpRequest.newBuilder()
                .uri(URI.create(seleniumBase + path))
                .timeout(timeout)
                .header("Content-Type", "application/json");

        if (body != null) {
            String json = body instanceof String s ? s : mapper.writeValueAsString(body);
            reqBuilder.method(method, HttpRequest.BodyPublishers.ofString(json));
        } else if ("DELETE".equals(method)) {
            reqBuilder.DELETE();
        } else {
            reqBuilder.GET();
        }

        HttpResponse<String> resp = httpClient.send(reqBuilder.build(), HttpResponse.BodyHandlers.ofString());
        JsonNode data = mapper.readTree(resp.body());
        JsonNode value = data.path("value");
        if (value.has("error")) {
            throw new IOException("WebDriver " + value.path("error").asText() + ": " + value.path("message").asText());
        }
        return value;
    }

    public JsonNode wdGet(String path) throws IOException, InterruptedException {
        return wdFetch(path, "GET", null, Duration.ofSeconds(30));
    }

    public JsonNode wdPost(String path, Object body) throws IOException, InterruptedException {
        return wdFetch(path, "POST", body, Duration.ofSeconds(30));
    }

    public JsonNode wdPost(String path, Object body, Duration timeout) throws IOException, InterruptedException {
        return wdFetch(path, "POST", body, timeout);
    }

    public void wdDelete(String path) throws IOException, InterruptedException {
        wdFetch(path, "DELETE", null, Duration.ofSeconds(5));
    }

    // ------------------------------------------------------------------
    // Session management
    // ------------------------------------------------------------------

    public String ensureSession() throws IOException, InterruptedException {
        sessionLock.lock();
        try {
            return ensureSessionImpl();
        } finally {
            sessionLock.unlock();
        }
    }

    private String ensureSessionImpl() throws IOException, InterruptedException {
        if (sessionId != null) {
            try {
                wdFetch("/session/" + sessionId + "/url", "GET", null, Duration.ofSeconds(5));
                return sessionId;
            } catch (Exception e) {
                sessionId = null;
            }
        }

        String existing = findExistingSession();
        if (existing != null) {
            log.info("Reusing existing session: {}", existing);
            sessionId = existing;
            try {
                wdFetch("/session/" + sessionId + "/url", "GET", null, Duration.ofSeconds(5));
                return sessionId;
            } catch (Exception e) {
                log.warn("Existing session dead, cleaning up");
                cleanupStaleSession(existing);
                sessionId = null;
            }
        }

        log.info("Creating new WebDriver session...");
        var caps = mapper.createObjectNode();
        var always = caps.putObject("capabilities").putObject("alwaysMatch");
        always.put("browserName", "chrome");
        var chrome = always.putObject("goog:chromeOptions");
        var args = chrome.putArray("args");
        for (String a : List.of(
                "--no-sandbox", "--test-type", "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars", "--window-size=1366,768", "--lang=zh-CN"
        )) {
            args.add(a);
        }
        chrome.putArray("excludeSwitches").add("enable-automation");
        chrome.put("useAutomationExtension", false);

        JsonNode result = wdPost("/session", caps, Duration.ofSeconds(15));
        sessionId = result.path("sessionId").asText();
        log.info("Session created: {}", sessionId);

        try {
            var rect = mapper.createObjectNode().put("width", 1366).put("height", 768);
            wdPost("/session/" + sessionId + "/window/rect", rect);
        } catch (Exception ignored) {}

        injectStealth(sessionId);
        return sessionId;
    }

    private String findExistingSession() {
        try {
            JsonNode status = wdGet("/status");
            JsonNode nodes = status.path("nodes");
            if (nodes.isArray()) {
                for (JsonNode node : nodes) {
                    for (JsonNode slot : node.path("slots")) {
                        String sid = slot.path("session").path("sessionId").asText(null);
                        if (sid != null && !sid.isEmpty()) return sid;
                    }
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    private void cleanupStaleSession(String sid) {
        try { wdDelete("/session/" + sid); } catch (Exception ignored) {}
    }

    private void injectStealth(String sid) throws IOException, InterruptedException {
        String script = jsScripts.stealth();
        try {
            var cdpCmd = mapper.createObjectNode()
                    .put("cmd", "Page.addScriptToEvaluateOnNewDocument");
            cdpCmd.putObject("params").put("source", script);
            wdPost("/session/" + sid + "/goog/cdp/execute", cdpCmd, Duration.ofSeconds(5));
            log.info("Stealth: addScriptToEvaluateOnNewDocument OK");
        } catch (Exception e) {
            log.warn("Stealth CDP inject failed: {}", e.getMessage());
        }

        try {
            executeScript(sid, "return " + script, List.of());
        } catch (Exception ignored) {}

        try {
            var tz = mapper.createObjectNode()
                    .put("cmd", "Emulation.setTimezoneOverride");
            tz.putObject("params").put("timezoneId", "Asia/Shanghai");
            wdPost("/session/" + sid + "/goog/cdp/execute", tz, Duration.ofSeconds(5));
            log.info("Stealth: timezone -> Asia/Shanghai");
        } catch (Exception ignored) {}
    }

    // ------------------------------------------------------------------
    // Script execution
    // ------------------------------------------------------------------

    public JsonNode executeScript(String script, List<Object> args)
            throws IOException, InterruptedException {
        return executeScript(ensureSession(), script, args);
    }

    public JsonNode executeScript(String sid, String script, List<Object> args)
            throws IOException, InterruptedException {
        var body = mapper.createObjectNode();
        body.put("script", script);
        body.set("args", mapper.valueToTree(args));
        return wdPost("/session/" + sid + "/execute/sync", body);
    }

    // ------------------------------------------------------------------
    // Actions
    // ------------------------------------------------------------------

    public void performActions(List<Map<String, Object>> actions)
            throws IOException, InterruptedException {
        String sid = ensureSession();
        wdPost("/session/" + sid + "/actions", Map.of("actions", actions));
        wdDelete("/session/" + sid + "/actions");
    }

    // ------------------------------------------------------------------
    // Tab management
    // ------------------------------------------------------------------

    public List<String> getWindowHandles() throws IOException, InterruptedException {
        String sid = ensureSession();
        JsonNode handles = wdGet("/session/" + sid + "/window/handles");
        List<String> result = new ArrayList<>();
        if (handles.isArray()) {
            for (JsonNode h : handles) result.add(h.asText());
        }
        return result;
    }

    public String getCurrentWindowHandle() throws IOException, InterruptedException {
        return wdGet("/session/" + ensureSession() + "/window").asText();
    }

    public void switchToWindow(String handle) throws IOException, InterruptedException {
        wdPost("/session/" + ensureSession() + "/window", Map.of("handle", handle));
    }

    public void closeCurrentWindow() throws IOException, InterruptedException {
        wdDelete("/session/" + ensureSession() + "/window");
    }

    public record TabSwitchResult(boolean newTabOpened, boolean autoSwitched, int tabCount, String switchedTo) {}

    public TabSwitchResult detectAndSwitchNewTab(List<String> handlesBefore, int timeoutMs)
            throws IOException, InterruptedException {
        String sid = ensureSession();
        long start = System.currentTimeMillis();
        List<String> lastHandles = handlesBefore;

        while (System.currentTimeMillis() - start < timeoutMs) {
            List<String> after = getWindowHandles();
            lastHandles = after;
            List<String> opened = after.stream().filter(h -> !handlesBefore.contains(h)).toList();
            if (!opened.isEmpty()) {
                String target = opened.get(opened.size() - 1);
                String current = getCurrentWindowHandle();
                if (!current.equals(target)) {
                    log.info("Auto-switching to new tab {} ({} tabs)", target, after.size());
                    switchToWindow(target);
                }
                return new TabSwitchResult(true, true, after.size(), target);
            }
            Thread.sleep(200);
        }
        return new TabSwitchResult(lastHandles.size() > handlesBefore.size(), false, lastHandles.size(), null);
    }

    // ------------------------------------------------------------------
    // Quick observe helper
    // ------------------------------------------------------------------

    public Map<String, Object> quickObserve() {
        try {
            JsonNode result = executeScript(jsScripts.observe(), List.of());
            return Map.of(
                    "url", result.path("url").asText(""),
                    "title", result.path("title").asText(""),
                    "elementCount", result.path("elementCount").asInt(0)
            );
        } catch (Exception e) {
            return Map.of("url", "(observe failed)", "title", "", "elementCount", 0);
        }
    }

    // ------------------------------------------------------------------
    // Navigation helpers
    // ------------------------------------------------------------------

    public void navigate(String url) throws IOException, InterruptedException {
        wdPost("/session/" + ensureSession() + "/url", Map.of("url", url), Duration.ofSeconds(60));
    }

    public String getUrl() throws IOException, InterruptedException {
        return wdGet("/session/" + ensureSession() + "/url").asText();
    }

    public String getTitle() throws IOException, InterruptedException {
        return wdGet("/session/" + ensureSession() + "/title").asText();
    }

    public ObjectMapper getMapper() { return mapper; }
}
