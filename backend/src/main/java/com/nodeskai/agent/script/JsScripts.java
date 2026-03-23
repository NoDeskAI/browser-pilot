package com.nodeskai.agent.script;

import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

@Component
public class JsScripts {

    private String observeScript;
    private String clickTextScript;
    private String clickElementScript;
    private String tripleSuccessCheckScript;
    private String stealthScript;

    @PostConstruct
    void init() throws IOException {
        observeScript = load("scripts/observe.js");
        clickTextScript = load("scripts/click-text.js");
        clickElementScript = load("scripts/click-element.js");
        tripleSuccessCheckScript = load("scripts/triple-success-check.js");
        stealthScript = load("scripts/stealth.js");
    }

    private String load(String path) throws IOException {
        return new ClassPathResource(path).getContentAsString(StandardCharsets.UTF_8);
    }

    public String observe()            { return observeScript; }
    public String clickText()          { return clickTextScript; }
    public String clickElement()       { return clickElementScript; }
    public String tripleSuccessCheck() { return tripleSuccessCheckScript; }
    public String stealth()            { return stealthScript; }
}
