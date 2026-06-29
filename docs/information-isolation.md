# Information Isolation

The app forbids hidden information leakage by construction:

- AI prompts are built only through `build_ai_context`.
- `build_ai_context` uses `PublicState` plus the target player's `PlayerPrivateView`.
- ORM objects and full database rows are never serialized into prompts.
- AI memories are keyed by player id and never shared.
- Private chat events use `PRIVATE_CHAT_PARTICIPANTS` scope.
- Full assignments are only returned by postgame reveal or `DEV_REVEAL=true`.
- Public observation memory is derived only from public speech, nominations, votes, and each AI's own memory record.
- `TableNotebook`, `CandidateScore`, and `WorldHypothesis` are built per AI from public events, that AI's private events, and that AI's memory only.
- Mock AI night kills do not select targets by reading hidden alignment; they use visible suspicion, public claims, and pressure summaries.

Security tests include:

- `test_player_view_never_contains_other_true_roles`
- `test_public_state_never_contains_hidden_alignment`
- `test_ai_context_does_not_contain_truth_state`
- `test_private_chat_only_visible_to_participants`
- `test_postgame_reveal_only_after_game_over`
- `test_dev_reveal_disabled_by_default`
- `test_mock_night_target_uses_memory_not_hidden_alignment`
- `test_nomination_updates_ai_suspicion_without_truth`
- `test_table_notebook_contains_public_claims_without_truth`
- `test_refresh_ai_brain_updates_isolated_memory_only`
