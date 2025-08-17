#!/bin/bash
# Make script executable: chmod +x test_polling.sh

# Test script for polling endpoints
# Run this after deploying to Render

BASE_URL="https://coda-ai-web.onrender.com"

echo "Testing polling endpoints..."

# Test 1: Start small analysis (should complete immediately)
echo "1. Testing small content analysis..."
RESPONSE=$(curl -s -X POST "$BASE_URL/request" \
  -H "Content-Type: application/json" \
  -d '{
    "record_id": "test-123",
    "content": "This is a small test content for immediate processing.",
    "user_prompt": "Please analyze this content and provide 3 key insights.",
    "system_prompt": "You are a helpful analyst.",
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 1000,
    "temperature": 0.3
  }')

echo "Response: $RESPONSE"

# Extract job_id from response
JOB_ID=$(echo $RESPONSE | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
echo "Job ID: $JOB_ID"

if [ ! -z "$JOB_ID" ]; then
  # Test 2: Get results
  echo -e "\n2. Getting results..."
  curl -s "$BASE_URL/response/$JOB_ID" | jq
else
  echo "No job ID found, checking direct response..."
fi

# Test 3: Large content (should queue for async processing)
echo -e "\n3. Testing large content analysis..."
LARGE_CONTENT=$(printf "This is a longer piece of content for testing. %.0s" {1..500})

RESPONSE2=$(curl -s -X POST "$BASE_URL/request" \
  -H "Content-Type: application/json" \
  -d "{
    \"record_id\": \"test-456\",
    \"content\": \"$LARGE_CONTENT\",
    \"user_prompt\": \"Analyze this content thoroughly.\",
    \"model\": \"claude-3-5-sonnet-20241022\"
  }")

echo "Large content response: $RESPONSE2"

JOB_ID2=$(echo $RESPONSE2 | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
if [ ! -z "$JOB_ID2" ]; then
  echo "Job ID for large content: $JOB_ID2"
  echo "Check results later with: curl $BASE_URL/response/$JOB_ID2"
fi

echo -e "\nTest completed!"
