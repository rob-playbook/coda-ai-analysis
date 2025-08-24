import * as coda from "@codahq/packs-sdk";

export const pack = coda.newPack();

// Network domain for Render service
pack.addNetworkDomain("coda-ai-web.onrender.com");

// Pack information formula
pack.addFormula({
  name: "PackInfo",
  description: "Pack information",
  parameters: [],
  resultType: coda.ValueType.String,
  execute: async function ([], context) {
    return "GRID API - Render AI Analysis Pack - v1.0";
  }
});

// PROPER FIX: Use cacheBreaker in the actual API call
pack.addFormula({
  name: "StartAnalysis",
  description: "Start AI analysis processing - returns JSON string",
  parameters: [
    // Cache-busting parameter - MUST BE USED IN THE API CALL
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "cacheBreaker",
      description: "Timestamp to prevent caching - use Now().ToText()"
    }),
    // Core parameters
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "recordId",
      description: "Record ID"
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "content",
      description: "Content to analyze"
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "userPrompt",
      description: "User prompt"
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "systemPrompt",
      description: "System prompt",
      optional: true
    }),
    // API configuration parameters
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "model",
      description: "Claude model",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.Number,
      name: "maxTokens",
      description: "Max tokens",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.Number,
      name: "temperature",
      description: "Temperature",
      optional: true
    }),
    // Extended thinking parameters
    coda.makeParameter({
      type: coda.ParameterType.Boolean,
      name: "extendedThinking",
      description: "Enable extended thinking",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.Number,
      name: "thinkingBudget",
      description: "Thinking budget tokens",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.Boolean,
      name: "includeThinking",
      description: "Include thinking in response",
      optional: true
    })
  ],
  resultType: coda.ValueType.String,
  execute: async function ([cacheBreaker, recordId, content, userPrompt, systemPrompt, model, maxTokens, temperature, extendedThinking, thinkingBudget, includeThinking], context) {
    try {
      // KEY FIX: Include cacheBreaker in the API call to make each request unique
      const payload = {
        record_id: recordId + "-" + cacheBreaker,  // Include timestamp in record_id
        content: content,
        user_prompt: userPrompt,
        system_prompt: systemPrompt || "",
        model: model || "claude-3-7-sonnet-latest",
        max_tokens: maxTokens || 14000,
        temperature: temperature || 0.7,
        extended_thinking: extendedThinking || false,
        thinking_budget: thinkingBudget || null,
        include_thinking: includeThinking || false
      };

      const response = await context.fetcher.fetch({
        method: "POST",
        url: "https://coda-ai-web.onrender.com/request",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      });

      return JSON.stringify(response.body);
    } catch (error) {
      return JSON.stringify({
        job_id: "",
        status: "failed",
        message: `Error: ${error.message}`,
        error_message: error.message
      });
    }
  }
});

// CheckResults formula 
pack.addFormula({
  name: "CheckResults",
  description: "Check analysis results",
  parameters: [
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "jobId",
      description: "Job ID"
    })
  ],
  resultType: coda.ValueType.String,
  execute: async function ([jobId], context) {
    try {
      const response = await context.fetcher.fetch({
        method: "GET",
        url: `https://coda-ai-web.onrender.com/response/${jobId}`
      });

      return JSON.stringify(response.body);
    } catch (error) {
      return JSON.stringify({
        job_id: jobId,
        status: "failed",
        message: `Error: ${error.message}`,
        error_message: error.message
      });
    }
  }
});
