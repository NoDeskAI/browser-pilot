package com.nodeskai.agent.factory;

import com.nodeskai.agent.model.ChatRequest;
import dev.langchain4j.model.chat.StreamingChatModel;
import dev.langchain4j.model.anthropic.AnthropicStreamingChatModel;
import dev.langchain4j.model.openai.OpenAiStreamingChatModel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

@Component
public class LlmFactory {

    private static final Logger log = LoggerFactory.getLogger(LlmFactory.class);

    public StreamingChatModel create(ChatRequest request) {
        String base = request.baseUrl() != null ? request.baseUrl().replaceAll("/+$", "") : "https://api.openai.com/v1";
        String model = request.model() != null ? request.model() : "gpt-4o-mini";
        String apiType = request.apiType() != null ? request.apiType() : "openai";
        String apiKey = request.apiKey();

        log.info("Creating LLM: base={}, model={}, apiType={}", base, model, apiType);

        if ("anthropic".equalsIgnoreCase(apiType)) {
            String anthropicBase = base.endsWith("/v1") ? base : base + "/v1";
            return AnthropicStreamingChatModel.builder()
                    .baseUrl(anthropicBase)
                    .apiKey(apiKey)
                    .modelName(model.isBlank() ? "claude-sonnet-4-20250514" : model)
                    .build();
        }

        return OpenAiStreamingChatModel.builder()
                .baseUrl(base)
                .apiKey(apiKey)
                .modelName(model)
                .build();
    }
}
