package com.nodeskai.agent.factory;

import com.nodeskai.agent.model.ChatRequest;
import dev.langchain4j.http.client.apache.ApacheHttpClientBuilder;
import dev.langchain4j.model.chat.StreamingChatModel;
import dev.langchain4j.model.anthropic.AnthropicStreamingChatModel;
import dev.langchain4j.model.openai.OpenAiStreamingChatModel;
import org.apache.hc.client5.http.impl.classic.HttpClientBuilder;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManagerBuilder;
import org.apache.hc.client5.http.ssl.NoopHostnameVerifier;
import org.apache.hc.client5.http.ssl.SSLConnectionSocketFactoryBuilder;
import org.apache.hc.core5.util.Timeout;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.time.Duration;

@Component
public class LlmFactory {

    private static final Logger log = LoggerFactory.getLogger(LlmFactory.class);

    private final SSLContext sslContext;

    public LlmFactory() {
        try {
            TrustManager[] trustAll = {new X509TrustManager() {
                public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
                public void checkClientTrusted(X509Certificate[] c, String t) {}
                public void checkServerTrusted(X509Certificate[] c, String t) {}
            }};
            sslContext = SSLContext.getInstance("TLSv1.2");
            sslContext.init(null, trustAll, new SecureRandom());
            log.info("SSL context initialized with TLSv1.2");
        } catch (Exception e) {
            throw new RuntimeException("Failed to init SSL context", e);
        }
    }

    private ApacheHttpClientBuilder apacheHttpClient() {
        var sslSocketFactory = SSLConnectionSocketFactoryBuilder.create()
                .setSslContext(sslContext)
                .setHostnameVerifier(NoopHostnameVerifier.INSTANCE)
                .build();
        var connManager = PoolingHttpClientConnectionManagerBuilder.create()
                .setSSLSocketFactory(sslSocketFactory)
                .setDefaultConnectionConfig(org.apache.hc.client5.http.config.ConnectionConfig.custom()
                        .setConnectTimeout(Timeout.ofSeconds(30))
                        .setSocketTimeout(Timeout.ofSeconds(120))
                        .build())
                .setMaxConnTotal(20)
                .setMaxConnPerRoute(10)
                .build();
        var httpClientBuilder = HttpClientBuilder.create()
                .setConnectionManager(connManager);
        return new ApacheHttpClientBuilder()
                .httpClientBuilder(httpClientBuilder)
                .connectTimeout(Duration.ofSeconds(30))
                .readTimeout(Duration.ofSeconds(120));
    }

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
                    .httpClientBuilder(apacheHttpClient())
                    .build();
        }

        return OpenAiStreamingChatModel.builder()
                .baseUrl(base)
                .apiKey(apiKey)
                .modelName(model)
                .httpClientBuilder(apacheHttpClient())
                .build();
    }
}
