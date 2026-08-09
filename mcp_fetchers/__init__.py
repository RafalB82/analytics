"""mcp_fetchers — deterministyczne konwersje: surowe dane MCP -> format analytics.

Rozdzielenie odpowiedzialności (patrz README -> Filozofia):
  agent (MCP pobieranie) -> *_normalize.py (czysta konwersja, offline) ->
  build_input.py (składa payload) -> analytics.run_analysis (rdzeń).
"""
