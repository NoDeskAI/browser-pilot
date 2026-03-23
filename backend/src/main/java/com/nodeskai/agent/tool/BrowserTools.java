package com.nodeskai.agent.tool;

import com.fasterxml.jackson.databind.JsonNode;
import com.nodeskai.agent.script.JsScripts;
import com.nodeskai.agent.service.WebDriverClient;
import dev.langchain4j.agent.tool.P;
import dev.langchain4j.agent.tool.Tool;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.*;

@Component
public class BrowserTools {

    private static final Logger log = LoggerFactory.getLogger(BrowserTools.class);

    private final WebDriverClient wd;
    private final JsScripts js;

    public BrowserTools(WebDriverClient wd, JsScripts js) {
        this.wd = wd;
        this.js = js;
    }

    // ------------------------------------------------------------------
    // Navigation
    // ------------------------------------------------------------------

    @Tool("在远程浏览器当前标签页中导航到指定 URL")
    public Map<String, Object> browser_navigate(
            @P("要访问的完整 URL") String url
    ) {
        try {
            wd.navigate(url);
            Thread.sleep(1500);
            return Map.of("ok", true, "navigatedTo", url, "currentPage", wd.quickObserve());
        } catch (Exception e) {
            return Map.of("ok", false, "error", e.getMessage());
        }
    }

    // ------------------------------------------------------------------
    // Tab management
    // ------------------------------------------------------------------

    @Tool("列出远程浏览器当前打开的所有标签页")
    public Map<String, Object> browser_list_tabs() {
        try {
            List<String> handles = wd.getWindowHandles();
            String current = wd.getCurrentWindowHandle();
            List<Map<String, Object>> tabs = new ArrayList<>();
            for (String h : handles) {
                wd.switchToWindow(h);
                tabs.add(Map.of(
                        "handle", h,
                        "url", wd.getUrl(),
                        "title", wd.getTitle(),
                        "active", h.equals(current)
                ));
            }
            wd.switchToWindow(current);
            return Map.of("ok", true, "tabs", tabs, "count", tabs.size());
        } catch (Exception e) {
            return Map.of("ok", false, "error", e.getMessage());
        }
    }

    @Tool("切换到指定标签页（通过 handle 或索引）")
    public Map<String, Object> browser_switch_tab(
            @P("目标标签页的 handle") String handle,
            @P("目标标签页索引（0 为第一个，-1 为最后一个）") Integer index,
            @P("切换前是否关闭当前标签页") Boolean closeCurrent
    ) {
        try {
            List<String> handles = wd.getWindowHandles();
            String target;
            if (handle != null && !handle.isEmpty()) {
                if (!handles.contains(handle)) return Map.of("ok", false, "error", "Handle not found");
                target = handle;
            } else if (index != null) {
                int idx = index < 0 ? handles.size() + index : index;
                if (idx < 0 || idx >= handles.size()) return Map.of("ok", false, "error", "Index out of range");
                target = handles.get(idx);
            } else {
                return Map.of("ok", false, "error", "Must provide handle or index");
            }
            if (Boolean.TRUE.equals(closeCurrent)) wd.closeCurrentWindow();
            wd.switchToWindow(target);
            return Map.of("ok", true, "switchedTo", target, "currentPage", wd.quickObserve());
        } catch (Exception e) {
            return Map.of("ok", false, "error", e.getMessage());
        }
    }

    // ------------------------------------------------------------------
    // Observe
    // ------------------------------------------------------------------

    @Tool("观察当前页面：获取 URL、标题、可见文本、所有可见的交互元素及其坐标。用这个来'看'页面。")
    public Map<String, Object> browser_observe() {
        try {
            JsonNode result = wd.executeScript(js.observe(), List.of());
            return wd.getMapper().convertValue(result, Map.class);
        } catch (Exception e) {
            return Map.of("error", e.getMessage());
        }
    }

    // ------------------------------------------------------------------
    // Click
    // ------------------------------------------------------------------

    @Tool("在远程浏览器页面上点击指定坐标。如果点击导致新标签页打开，会自动切换到新标签页。")
    public Map<String, Object> browser_click(
            @P("点击的 X 坐标") int x,
            @P("点击的 Y 坐标") int y,
            @P("按住时长（毫秒），>0 时执行长按") Integer holdMs
    ) {
        try {
            List<String> handlesBefore = wd.getWindowHandles();
            int hold = clamp(holdMs, 0, 10_000);

            List<Map<String, Object>> pointerActions;
            if (hold > 0) {
                pointerActions = List.of(
                        pointerMove(80, x, y),
                        pause(80 + rand(120)),
                        pointerDown(), pause(hold), pointerUp()
                );
            } else {
                pointerActions = humanClickActions(x, y);
            }
            wd.performActions(List.of(pointerInput("mouse", pointerActions)));
            Thread.sleep(500);

            var sw = wd.detectAndSwitchNewTab(handlesBefore, 3200);
            if (sw.autoSwitched()) Thread.sleep(600);
            var page = wd.quickObserve();
            return buildClickResult(x, y, hold, sw, page);
        } catch (Exception e) {
            return Map.of("ok", false, "error", e.getMessage());
        }
    }

    @Tool("通过可见文字内容查找并点击元素。优先使用此工具来点击按钮和链接。")
    public Map<String, Object> browser_click_text(
            @P("要查找的元素可见文字，如'登录'、'搜索'、'点赞'") String text,
            @P("是否精确匹配（默认模糊匹配）") Boolean exact,
            @P("按住时长（毫秒），>0 时在元素中心长按") Integer holdMs
    ) {
        try {
            List<String> handlesBefore = wd.getWindowHandles();
            int hold = clamp(holdMs, 0, 10_000);
            boolean doClick = hold <= 0;

            JsonNode clickResult = wd.executeScript(js.clickText(), List.of(text, exact != null && exact, doClick));
            if (!clickResult.path("found").asBoolean()) {
                return Map.of("ok", false, "error", "No element with text \"" + text + "\" found");
            }

            if (hold > 0) {
                int cx = clickResult.path("x").asInt();
                int cy = clickResult.path("y").asInt();
                wd.performActions(List.of(pointerInput("mouse", List.of(
                        pointerMove(80, cx, cy), pause(80 + rand(120)),
                        pointerDown(), pause(hold), pointerUp()
                ))));
            }

            Thread.sleep(500);
            var sw = wd.detectAndSwitchNewTab(handlesBefore, 3200);
            if (sw.autoSwitched()) Thread.sleep(600);
            var page = wd.quickObserve();

            Map<String, Object> result = new LinkedHashMap<>();
            result.put("ok", true);
            result.put("text", text);
            result.put("holdMs", hold);
            result.put("clicked", wd.getMapper().convertValue(clickResult, Map.class));
            result.put("newTabOpened", sw.newTabOpened());
            result.put("autoSwitched", sw.autoSwitched());
            result.put("tabCount", sw.tabCount());
            result.put("currentPage", page);
            return result;
        } catch (Exception e) {
            return Map.of("ok", false, "error", e.getMessage());
        }
    }

    @Tool("通过 CSS 选择器查找并点击元素，支持 shadow DOM 和同源 iframe。")
    public Map<String, Object> browser_click_element(
            @P("CSS 选择器") String selector
    ) {
        try {
            List<String> handlesBefore = wd.getWindowHandles();
            JsonNode clickResult = wd.executeScript(js.clickElement(), List.of(selector));
            if (!clickResult.path("found").asBoolean()) {
                return Map.of("ok", false, "error", "Element \"" + selector + "\" not found");
            }
            Thread.sleep(500);
            var sw = wd.detectAndSwitchNewTab(handlesBefore, 3200);
            if (sw.autoSwitched()) Thread.sleep(600);
            var page = wd.quickObserve();

            Map<String, Object> result = new LinkedHashMap<>();
            result.put("ok", true);
            result.put("selector", selector);
            result.put("clicked", wd.getMapper().convertValue(clickResult, Map.class));
            result.put("newTabOpened", sw.newTabOpened());
            result.put("autoSwitched", sw.autoSwitched());
            result.put("tabCount", sw.tabCount());
            result.put("currentPage", page);
            return result;
        } catch (Exception e) {
            return Map.of("ok", false, "error", e.getMessage());
        }
    }

    // ------------------------------------------------------------------
    // Triple-like (Bilibili)
    // ------------------------------------------------------------------

    @Tool("【一键三连专用工具】在B站视频页执行一键三连。自动完成：定位点赞按钮→长按3秒以上→检测三连成功。当用户要求三连时必须使用此工具。")
    public Map<String, Object> browser_triple_like(
            @P("长按时长毫秒，默认3200") Integer holdMs,
            @P("点赞按钮文本，默认'点赞'") String buttonText
    ) {
        try {
            int pressMs = Math.max(3000, Math.min(10_000, holdMs != null ? holdMs : 3200));
            String likeText = buttonText != null && !buttonText.isBlank() ? buttonText.trim() : "点赞";

            JsonNode findResult = wd.executeScript(js.clickText(), List.of(likeText, false, false));
            if (!findResult.path("found").asBoolean()) {
                return Map.of("ok", false, "error", "未找到点赞按钮（text=\"" + likeText + "\"）", "step", 1);
            }

            int fx = findResult.path("x").asInt();
            int fy = findResult.path("y").asInt();
            wd.performActions(List.of(pointerInput("mouse", List.of(
                    pointerMove(80, fx, fy), pause(120),
                    pointerDown(), pause(pressMs), pointerUp()
            ))));

            List<String> keywords = List.of("三连成功", "一键三连成功", "已完成一键三连", "三连完成");
            JsonNode detect = wd.executeScript(js.tripleSuccessCheck(), List.of(keywords));
            long deadline = System.currentTimeMillis() + 2000;
            while (!detect.path("matched").asBoolean() && System.currentTimeMillis() < deadline) {
                Thread.sleep(200);
                detect = wd.executeScript(js.tripleSuccessCheck(), List.of(keywords));
            }

            var page = wd.quickObserve();
            if (!detect.path("matched").asBoolean()) {
                return Map.of("ok", false, "step", 3,
                        "error", "未检测到三连成功字样",
                        "detectedSample", detect.path("sample").asText(""),
                        "currentPage", page);
            }

            return Map.of("ok", true, "step", 4,
                    "message", "点赞三连成功完成",
                    "matchedKeyword", detect.path("keyword").asText(),
                    "holdMs", pressMs,
                    "currentPage", page);
        } catch (Exception e) {
            return Map.of("ok", false, "error", e.getMessage());
        }
    }

    // ------------------------------------------------------------------
    // Type / Key
    // ------------------------------------------------------------------

    @Tool("在远程浏览器中输入文本（在当前聚焦的输入框中）")
    public Map<String, Object> browser_type(
            @P("要输入的文本") String text
    ) {
        try {
            wd.performActions(List.of(keyInput("keyboard", humanKeyActions(text))));
            return Map.of("ok", true, "typed", text, "currentPage", wd.quickObserve());
        } catch (Exception e) {
            return Map.of("ok", false, "error", e.getMessage());
        }
    }

    @Tool("在远程浏览器中按下键盘按键，如 Enter、Tab、Escape。如果按键导致新标签页打开，会自动切换过去。")
    public Map<String, Object> browser_key(
            @P("按键名称，如 Enter、Tab、Escape") String key
    ) {
        try {
            List<String> handlesBefore = wd.getWindowHandles();
            String keyValue = WebDriverClient.KEY_MAP.getOrDefault(key, key);
            wd.performActions(List.of(keyInput("keyboard", List.of(
                    Map.of("type", "keyDown", "value", keyValue),
                    Map.of("type", "keyUp", "value", keyValue)
            ))));
            Thread.sleep(800);
            var sw = wd.detectAndSwitchNewTab(handlesBefore, 2500);
            if (sw.autoSwitched()) Thread.sleep(600);

            Map<String, Object> result = new LinkedHashMap<>();
            result.put("ok", true);
            result.put("key", key);
            result.put("newTabOpened", sw.newTabOpened());
            result.put("autoSwitched", sw.autoSwitched());
            result.put("tabCount", sw.tabCount());
            result.put("currentPage", wd.quickObserve());
            return result;
        } catch (Exception e) {
            return Map.of("ok", false, "error", e.getMessage());
        }
    }

    // ------------------------------------------------------------------
    // Scroll
    // ------------------------------------------------------------------

    @Tool("在远程浏览器页面上滚动")
    public Map<String, Object> browser_scroll(
            @P("滚动起始 X 坐标") Integer x,
            @P("滚动起始 Y 坐标") Integer y,
            @P("水平滚动量") Integer deltaX,
            @P("垂直滚动量（正值向下）") int deltaY
    ) {
        try {
            int sx = x != null ? x : 640;
            int sy = y != null ? y : 360;
            int dx = deltaX != null ? deltaX : 0;
            wd.performActions(List.of(Map.of(
                    "type", "wheel", "id", "wheel",
                    "actions", List.of(Map.of(
                            "type", "scroll", "x", sx, "y", sy,
                            "deltaX", dx, "deltaY", deltaY,
                            "duration", 100, "origin", "viewport"
                    ))
            )));
            return Map.of("ok", true, "deltaX", dx, "deltaY", deltaY);
        } catch (Exception e) {
            return Map.of("ok", false, "error", e.getMessage());
        }
    }

    // ------------------------------------------------------------------
    // Page info
    // ------------------------------------------------------------------

    @Tool("获取远程浏览器当前页面的 URL 和标题")
    public Map<String, Object> browser_get_page_info() {
        try {
            return Map.of("url", wd.getUrl(), "title", wd.getTitle());
        } catch (Exception e) {
            return Map.of("error", e.getMessage());
        }
    }

    // ==================================================================
    // Human-like input helpers
    // ==================================================================

    private static final Random RNG = new Random();

    private static int rand(int bound) { return RNG.nextInt(bound); }
    private static int clamp(Integer v, int min, int max) {
        int val = v != null ? v : 0;
        return Math.max(min, Math.min(max, val));
    }

    static List<Map<String, Object>> humanClickActions(int x, int y) {
        int steps = 3 + rand(4);
        int startX = x + rand(120) - 60;
        int startY = y + rand(120) - 60;
        int cpX = (startX + x) / 2 + rand(40) - 20;
        int cpY = (startY + y) / 2 + rand(40) - 20;

        List<Map<String, Object>> actions = new ArrayList<>();
        for (int i = 0; i <= steps; i++) {
            double t = (double) i / steps;
            int px = (int) ((1 - t) * (1 - t) * startX + 2 * (1 - t) * t * cpX + t * t * x);
            int py = (int) ((1 - t) * (1 - t) * startY + 2 * (1 - t) * t * cpY + t * t * y);
            actions.add(pointerMove(15 + rand(35), Math.max(0, px), Math.max(0, py)));
        }
        actions.add(pointerMove(10, x + rand(3) - 1, y + rand(3) - 1));
        actions.add(pause(15 + rand(50)));
        actions.add(pointerDown());
        actions.add(pause(30 + rand(60)));
        actions.add(pointerUp());
        return actions;
    }

    static List<Map<String, Object>> humanKeyActions(String text) {
        List<Map<String, Object>> actions = new ArrayList<>();
        for (int i = 0; i < text.length(); i++) {
            String ch = String.valueOf(text.charAt(i));
            if (!actions.isEmpty()) actions.add(pause(30 + rand(90)));
            actions.add(Map.of("type", "keyDown", "value", ch));
            actions.add(pause(8 + rand(25)));
            actions.add(Map.of("type", "keyUp", "value", ch));
        }
        return actions;
    }

    // Action builder helpers
    private static Map<String, Object> pointerMove(int duration, int x, int y) {
        return Map.of("type", "pointerMove", "duration", duration, "x", x, "y", y, "origin", "viewport");
    }
    private static Map<String, Object> pause(int duration) {
        return Map.of("type", "pause", "duration", duration);
    }
    private static Map<String, Object> pointerDown() {
        return Map.of("type", "pointerDown", "button", 0);
    }
    private static Map<String, Object> pointerUp() {
        return Map.of("type", "pointerUp", "button", 0);
    }

    private static Map<String, Object> pointerInput(String id, List<Map<String, Object>> actions) {
        return Map.of("type", "pointer", "id", id,
                "parameters", Map.of("pointerType", "mouse"),
                "actions", actions);
    }
    private static Map<String, Object> keyInput(String id, List<Map<String, Object>> actions) {
        return Map.of("type", "key", "id", id, "actions", actions);
    }

    private Map<String, Object> buildClickResult(int x, int y, int hold,
                                                   WebDriverClient.TabSwitchResult sw,
                                                   Map<String, Object> page) {
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("ok", true);
        r.put("clickedAt", Map.of("x", x, "y", y));
        r.put("holdMs", hold);
        r.put("newTabOpened", sw.newTabOpened());
        r.put("autoSwitched", sw.autoSwitched());
        r.put("tabCount", sw.tabCount());
        r.put("currentPage", page);
        return r;
    }
}
