# TypeSafe AI in AgentFlow

`route.py` asks TypeSafe AI's System One model, Jev, which of the project's options should take a task. Anyone who
uses or changes this integration works from the official documentation, not from memory or third-party pages:

- Start here: https://docs.typesafe.ai/introduction (index for tools: https://docs.typesafe.ai/llms.txt)
- API: https://docs.typesafe.ai/api.md - `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <key>`,
  body `{state, model, questions}`; errors 401 key, 422 validation, 429 rate limit, 529 overloaded (back off, retry)
- Questions: https://docs.typesafe.ai/primitives/choice.md (up to 255 options), https://docs.typesafe.ai/primitives/score.md
  (2-10 ordered levels), https://docs.typesafe.ai/primitives/noul.md, https://docs.typesafe.ai/primitives/advanced.md
- State: https://docs.typesafe.ai/concepts/state.md - text only; an object with named fields for most requests
- Confidence: https://docs.typesafe.ai/confidence.md
- Building: https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md, https://docs.typesafe.ai/patterns/intent-routing.md
- Models, limits, price: https://docs.typesafe.ai/models.md
- Keys: https://console.typesafe.ai/keys; SDK variables `TYPESAFE_API_KEY`, `TYPESAFE_BASE_URL`

## Rules from the documentation that route.py follows

1. Jev makes fast, atomic, typed judgments; it is not for extended reasoning. Decompose: several narrow questions in
   one request (they run in parallel), combined by deterministic code. route.py asks a choice "option" (which project
   option fits) and a 3-level score "complexity", and escalates a pick whose `max_complexity` is exceeded to its
   `escalate_to` option.
2. State is an object with named fields and only what the decision needs (the task's title, role, goal, acceptance
   criteria, allowed files, checks, repositories); questions and policy stay out of the state. The state budget is
   32k tokens (64k per request): long fields are clipped. English gives the best accuracy (Task Files are English).
3. Choice criteria are objects: `what`, `not_for`, `examples` draw the boundary between options. Write them in
   `model-options.json`; the project policy goes into the question's instructions.
4. Confidence: `(p_max - 1/n) / (1 - 1/n)` for a choice. Bands: >= 0.9 act automatically (`--apply`), 0.5-0.9 a
   recommendation the Orchestrator confirms, < 0.5 no decision (tool-routing.md decides). The docs say to start
   conservative and calibrate on your own results: keep `T-NNN.route.json` and compare it with how tasks went.
5. Pin the Jev version you calibrated on (`jev_model` in `model-options.json`, default `jev-1.13.0`); move to a new
   version on purpose, re-checking the bands.
6. Every call records Jev's version, token usage and `x-typesafe-request-id`; quote the request id to TypeSafe support.
   Price: input tokens only (see Models); one route call is about 1k input tokens.
7. The key is a secret: environment `TYPESAFE_API_KEY` or a file named by `AGENTFLOW_TYPESAFE_KEY_FILE`, outside the
   repository; never printed, logged or committed. Tests use a local stand-in server, never the real service.
