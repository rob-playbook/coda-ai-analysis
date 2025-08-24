# GRID API - Coda Pack

**Pack ID:** 43859  
**Pack Name:** GRID API  
**Network Domain:** coda-ai-web.onrender.com  

## Overview

This Coda Pack connects your Coda document to the Render-based AI analysis service, enabling unlimited content processing without Coda's 60-second timeout constraints.

## Key Fix Applied

**Problem Solved:** The pack was caching results when parameters were identical, leading to duplicate job IDs.

**Solution:** Added a `cacheBreaker` parameter as the first parameter to ensure each call is unique and prevents caching.

## Pack Functions

### StartAnalysis (Formula with Cache-Busting)
- **Purpose**: Start AI analysis processing  
- **Type**: Formula with cache-busting first parameter
- **Returns**: JSON string with job_id and status
- **Parameters**: 
  1. `cacheBreaker` (String) - Use `Now().ToText()` to prevent caching
  2. `recordId` (String) - Record identifier
  3. `content` (String) - Content to analyze  
  4. `userPrompt` (String) - User prompt
  5. `systemPrompt` (String, optional) - System prompt
  6. Plus other optional parameters...

### CheckResults (Formula)
- **Purpose**: Retrieve analysis results by job ID
- **Returns**: JSON string with analysis results
- **Parameters**: jobId (String)

### PackInfo (Formula)  
- **Purpose**: Pack version identification
- **Returns**: "GRID API - Render AI Analysis Pack - v1.0"

## Usage in Coda Formula

**IMPORTANT: Updated function signature!**

**OLD (causing caching):**
```javascript
Untitled::StartAnalysis(
  [DB AI Analysis].Last().[Row name].ToText(),
  ContentForAPI,
  UserPromptForAPI,
  // ...
)
```

**NEW (prevents caching):**
```javascript
Untitled::StartAnalysis(
  Now().ToText(),  // CACHE BREAKER - Always unique
  [DB AI Analysis].Last().[Row name].ToText(),
  ContentForAPI, 
  UserPromptForAPI,
  // ...
)
```

**Key Change:** Add `Now().ToText()` as the FIRST parameter to make each call unique.

## Development Commands

```bash
# Update SDK (if needed)
npm install @codahq/packs-sdk@latest

# Build pack
npm run build

# Validate pack  
npm run validate

# Upload to Coda
npm run upload
```

## Critical: Version Control

This pack source is now tracked in Git to prevent loss.

## Troubleshooting

**Still Getting Duplicate Job IDs?** 
- Ensure you're using `Now().ToText()` as the first parameter
- The timestamp makes each call unique, preventing caching

**SDK Errors?**
- Run `npm install @codahq/packs-sdk@latest` to update SDK
