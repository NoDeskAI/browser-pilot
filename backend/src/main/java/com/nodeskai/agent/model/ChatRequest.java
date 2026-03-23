package com.nodeskai.agent.model;

import java.util.List;
import java.util.Map;

public record ChatRequest(
        List<Map<String, String>> messages,
        String apiKey,
        String baseUrl,
        String model,
        String apiType
) {}
