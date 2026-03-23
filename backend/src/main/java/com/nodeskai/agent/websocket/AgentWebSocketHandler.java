package com.nodeskai.agent.websocket;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.nodeskai.agent.service.AgentService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.*;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

@Component
public class AgentWebSocketHandler extends TextWebSocketHandler {

    private static final Logger log = LoggerFactory.getLogger(AgentWebSocketHandler.class);

    private final AgentService agentService;
    private final ObjectMapper mapper;
    private final ExecutorService agentExecutor = Executors.newCachedThreadPool(r -> {
        Thread t = new Thread(r, "agent-worker");
        t.setDaemon(true);
        return t;
    });

    public AgentWebSocketHandler(AgentService agentService, ObjectMapper mapper) {
        this.agentService = agentService;
        this.mapper = mapper;
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        ConcurrentWebSocketSessionDecorator safeSession =
                new ConcurrentWebSocketSessionDecorator(session, 5000, 512 * 1024);
        agentService.registerSession(session.getId(), safeSession);
        log.info("WebSocket connected: {}", session.getId());
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        try {
            WsMessage msg = mapper.readValue(message.getPayload(), WsMessage.class);
            String action = msg.action();

            switch (action != null ? action : "") {
                case "chat" -> {
                    agentService.abort(session.getId());
                    Future<?> future = agentExecutor.submit(() ->
                            agentService.runAgent(msg.toChatRequest(), session.getId()));
                    agentService.registerRunning(session.getId(), future);
                }
                case "abort" -> agentService.abort(session.getId());
                default -> log.warn("Unknown WS action: {}", action);
            }
        } catch (Exception e) {
            log.error("Failed to handle WS message: {}", e.getMessage());
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        log.info("WebSocket closed: {} ({})", session.getId(), status);
        agentService.abort(session.getId());
        agentService.removeSession(session.getId());
    }

    @Override
    public void handleTransportError(WebSocketSession session, Throwable exception) {
        log.warn("WebSocket transport error for {}: {}", session.getId(), exception.getMessage());
        agentService.abort(session.getId());
    }
}
