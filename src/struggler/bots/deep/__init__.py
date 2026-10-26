"""The `deep` bot: DeepStruggle's network playing through struggler's API.

Struggler stays the referee. To decide, the player rebuilds the position in
DeepStruggle's own engine (`ts_engine`, native on Linux or WebAssembly
elsewhere), asks the network, and answers with one of struggler's legal
options. See docs/BOTS.md, "Deep bot".

Modules:

- `ids` maps countries, cards and sides between the two engines.
- `backend` is the narrow interface to a DeepStruggle engine build.
- `boundary` writes a struggler position at a headline or action-round start
  as a DeepStruggle `ts_save_v2` dict.
- `replay` groups struggler events into DeepStruggle micro-actions and steps
  them, with dice forced to the logged values.
"""
