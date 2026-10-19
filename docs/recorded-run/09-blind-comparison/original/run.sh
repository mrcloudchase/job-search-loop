#!/bin/bash
cd /tmp/run
export ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_API_KEY"
docker run --rm --user root --network host \
  -v /tmp/run/ai-job-search:/w -v /opt/node22:/opt/node22:ro -v /opt/claude-code:/opt/claude-code:ro -v /tmp/run/pylib:/pylib:ro -v /tmp/run/prompt.txt:/prompt.txt:ro \
  -e ANTHROPIC_AUTH_TOKEN -e ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic \
  -e ANTHROPIC_MODEL=deepseek-chat -e ANTHROPIC_SMALL_FAST_MODEL=deepseek-chat \
  -e ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-chat -e ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-chat -e ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-chat \
  -e CLAUDE_CODE_SUBAGENT_MODEL=deepseek-chat -e CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
  -e IS_SANDBOX=1 -e HOME=/root -e PYTHONPATH=/pylib -e PATH=/opt/node22/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  -w /w job-search-check:2 sh -c 'ln -sf /usr/bin/python3 /usr/local/bin/python; claude -p "$(cat /prompt.txt)" --model deepseek-chat --output-format stream-json --verbose --dangerously-skip-permissions'
