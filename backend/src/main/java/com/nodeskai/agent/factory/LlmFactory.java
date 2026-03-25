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
import org.springframework.beans.factory.annotation.Value;
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

    private final boolean sslTrustAll;
    private final SSLContext insecureSslContext;

    public LlmFactory(@Value("${app.ssl-trust-all:false}") boolean sslTrustAll) {
        this.sslTrustAll = sslTrustAll;
        if (sslTrustAll) {
            try {
                TrustManager[] trustAll = {new X509TrustManager() {
                    public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
                    public void checkClientTrusted(X509Certificate[] c, String t) {}
                    public void checkServerTrusted(X509Certificate[] c, String t) {}
                }};
                insecureSslContext = SSLContext.getInstance("TLSv1.2");
                insecureSslContext.init(null, trustAll, new SecureRandom());
                log.warn("SSL trust-all mode ENABLED (app.ssl-trust-all=true). Do NOT use in production!");
            } catch (Exception e) {
                throw new RuntimeException("Failed to init insecure SSL context", e);
            }
        } else {
            insecureSslContext = null;
            log.info("Using default JVM SSL/TLS trust store");
        }
    }

    private ApacheHttpClientBuilder apacheHttpClient() {
        var connManagerBuilder = PoolingHttpClientConnectionManagerBuilder.create()
                .setDefaultConnectionConfig(org.apache.hc.client5.http.config.ConnectionConfig.custom()
                        .setConnectTimeout(Timeout.ofSeconds(30))
                        .setSocketTimeout(Timeout.ofSeconds(120))
                        .build())
                .setMaxConnTotal(20)
                .setMaxConnPerRoute(10);

        if (sslTrustAll && insecureSslContext != null) {
            var sslSocketFactory = SSLConnectionSocketFactoryBuilder.create()
                    .setSslContext(insecureSslContext)
                    .setHostnameVerifier(NoopHostnameVerifier.INSTANCE)
                    .build();
            connManagerBuilder.setSSLSocketFactory(sslSocketFactory);
        }

        var connManager = connManagerBuilder.build();
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
