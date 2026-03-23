package com.nodeskai.agent.websocket;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.nodeskai.agent.model.ChatRequest;

import java.util.List;
import java.util.Map;

@JsonIgnoreProperties(ignoreUnknown = true)
public record WsMessage(
        String action,
        List<Map<String, String>> messages,
        String apiKey,
        String baseUrl,
        String model,
        String apiType
) {
    public ChatRequest toChatRequest() {
        return new ChatRequest(messages, apiKey, baseUrl, model, apiType);
    }
}
