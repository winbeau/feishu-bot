# Feishu-Dify Attachment Pipeline Roadmap

Date: 2026-04-28

## Goal

Support multi-turn Feishu text, image, and file messages through the existing
`UnifiedMessage`, `PlatformAdapter`, `LLMBackend`, and `Gateway.route()`
contracts without a broad architecture rewrite.

## Implemented Scope

- Wire Redis-backed `SessionStore` into the Feishu webhook after deduplication.
- Preserve Feishu `chat_id` for replies while passing the business session id to
  Gateway and Dify.
- Add attachment metadata to `UnifiedMessage`.
- Parse Feishu `text`, `image`, and `file` message payloads.
- Download Feishu image/file resources through the documented resource endpoint.
- Parse local `txt`, `md`, `csv`, `pptx`, and `pdf` files into text/tags.
- Build Dify `inputs` and remote image `files` without exposing local paths as
  remote URLs.
- Add a Redis-backed conversation summary store and Gateway integration.
- Process attachments before Gateway routing and reply with a fixed Chinese
  message when Feishu file download fails.
- Cover the main text, image, file, duplicate, Dify, parser, summary, and
  download paths with tests.

## Deferred

- Public file hosting or object storage for downloaded Feishu files.
- LLM-generated conversation summaries.
- WeChat and QQ attachment support.
