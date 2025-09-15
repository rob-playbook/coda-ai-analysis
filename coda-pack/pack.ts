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
      name: "source1",
      description: "Source content part 1"
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "source2",
      description: "Source content part 2",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "source3",
      description: "Source content part 3",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "source4",
      description: "Source content part 4",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "source5",
      description: "Source content part 5",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "source6",
      description: "Source content part 6",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "target1",
      description: "Target content part 1",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "target2",
      description: "Target content part 2",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "target3",
      description: "Target content part 3",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "target4",
      description: "Target content part 4",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "target5",
      description: "Target content part 5",
      optional: true
    }),
    coda.makeParameter({
      type: coda.ParameterType.String,
      name: "target6",
      description: "Target content part 6",
      optional: true
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
  execute: async function ([cacheBreaker, recordId, source1, source2, source3, source4, source5, source6, target1, target2, target3, target4, target5, target6, userPrompt, systemPrompt, model, maxTokens, temperature, extendedThinking, thinkingBudget, includeThinking], context) {
    try {
      // Send split pieces directly to render service (don't reconstruct locally)
      const payload = {
        record_id: recordId + "-" + cacheBreaker,  // Include timestamp in record_id
        source1: source1 || '',
        source2: source2 || null,
        source3: source3 || null,
        source4: source4 || null,
        source5: source5 || null,
        source6: source6 || null,
        target1: target1 || null,
        target2: target2 || null,
        target3: target3 || null,
        target4: target4 || null,
        target5: target5 || null,
        target6: target6 || null,
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
