# Tests

`python -m pytest -q` — 1250 cases, about 6 s. One suite per area; inside a suite, one `Test…` class per former
single-topic file, with that file's docstring (the incident or measurement it guards) kept as the class docstring.

- `conftest.py` — test-wide safety net (no test touches the network).
- `factories.py` — shared helpers: `ROOT`, `src(path)` (cached repo-file reader), `run_coro` (order-safe event loop), and the executor / simulator / recommendation factories.

| Suite | Cases |
|---|---|
| `tests/test_balance.py` | 102 |
| `tests/test_config_and_modes.py` | 92 |
| `tests/test_dashboard.py` | 86 |
| `tests/test_data_feed.py` | 94 |
| `tests/test_logs.py` | 35 |
| `tests/test_mirror_and_virtual_only.py` | 60 |
| `tests/test_notifications.py` | 43 |
| `tests/test_order_executor.py` | 137 |
| `tests/test_rate_limit.py` | 137 |
| `tests/test_risk_manager.py` | 96 |
| `tests/test_signal_filters.py` | 70 |
| `tests/test_startup_and_deploy.py` | 22 |
| `tests/test_symbol_registry.py` | 42 |
| `tests/test_virtual_simulator.py` | 102 |
| `tests/test_virtual_tracker.py` | 46 |
| `tests/test_weights.py` | 86 |

## Where the old test files went (consolidated 2026-09-29)

Older plans, specs and notes name the files below; each now lives in the class shown.

| Old file | Now |
|---|---|
| `tests/test_analysis_log.py` | `tests/test_logs.py::TestAnalysisLog` |
| `tests/test_backtest_results_per_mode.py` | `tests/test_config_and_modes.py::TestBacktestResultsPerMode` |
| `tests/test_balance_history.py` | `tests/test_balance.py::TestBalanceHistory` |
| `tests/test_balance_never_reads_as_zero.py` | `tests/test_balance.py::TestBalanceNeverReadsAsZero` |
| `tests/test_balance_prefetch_off_candle_boundary.py` | `tests/test_balance.py::TestBalancePrefetchOffCandleBoundary` |
| `tests/test_balance_ttl.py` | `tests/test_balance.py::TestBalanceTtl` |
| `tests/test_ban_alert_closure.py` | `tests/test_rate_limit.py::TestBanAlertClosure` |
| `tests/test_ban_alert_not_reannounced.py` | `tests/test_rate_limit.py::TestBanAlertNotReannounced` |
| `tests/test_ban_message_formatting.py` | `tests/test_rate_limit.py::TestBanMessageFormatting` |
| `tests/test_ban_state_persisted.py` | `tests/test_rate_limit.py::TestBanStatePersisted` |
| `tests/test_ban_window_not_clamped_to_an_hour.py` | `tests/test_rate_limit.py::TestBanWindowNotClampedToAnHour` |
| `tests/test_bookkeeping_tooltip.py` | `tests/test_dashboard.py::TestBookkeepingTooltip` |
| `tests/test_candle_check_skips_pre_entry_candle.py` | `tests/test_order_executor.py::TestCandleCheckSkipsPreEntryCandle` |
| `tests/test_compose_mirror_has_no_credentials.py` | `tests/test_mirror_and_virtual_only.py::TestComposeMirrorHasNoCredentials` |
| `tests/test_data_feed.py` | `tests/test_data_feed.py::TestDataFeed` |
| `tests/test_decision_log.py` | `tests/test_logs.py::TestDecisionLog` |
| `tests/test_decision_log_keeps_placed.py` | `tests/test_logs.py::TestDecisionLogKeepsPlaced` |
| `tests/test_disabled_symbols_persist_klines.py` | `tests/test_data_feed.py::TestDisabledSymbolsPersistKlines` |
| `tests/test_dockerfile_layer_order.py` | `tests/test_startup_and_deploy.py::TestDockerfileLayerOrder` |
| `tests/test_duplicate_skip_default.py` | `tests/test_signal_filters.py::TestTheBaseDefault` |
| `tests/test_entry_slippage_guard.py` | `tests/test_order_executor.py::TestEntrySlippageGuard` |
| `tests/test_failed_close_keeps_position.py` | `tests/test_order_executor.py::TestFailedCloseKeepsPosition` |
| `tests/test_fake_order_early_exit.py` | `tests/test_order_executor.py::TestFakeOrderEarlyExit` |
| `tests/test_fake_order_trail_activation.py` | `tests/test_order_executor.py::TestFakeOrderTrailActivation` |
| `tests/test_kline_endpoint_routing.py` | `tests/test_data_feed.py::TestKlineEndpointRouting` |
| `tests/test_kline_feed_fixes_2026_09_27.py` | `tests/test_data_feed.py::TestKlineFeedFixes20260927` |
| `tests/test_kline_refresh_retries_after_settling.py` | `tests/test_rate_limit.py::TestKlineRefreshRetriesAfterSettling` |
| `tests/test_kline_startup_weight.py` | `tests/test_data_feed.py::TestKlineStartupWeight` |
| `tests/test_last_known_balance_is_a_wallet_figure.py` | `tests/test_balance.py::TestLastKnownBalanceIsAWalletFigure` |
| `tests/test_leverage_scenario.py` | `tests/test_risk_manager.py::TestLeverageScenario` |
| `tests/test_leverage_tracker.py` | `tests/test_risk_manager.py::TestLeverageTracker` |
| `tests/test_lock_preset_targets_viewed_instance.py` | `tests/test_dashboard.py::TestLockPresetTargetsViewedInstance` |
| `tests/test_locked_presets_per_mode.py` | `tests/test_config_and_modes.py::TestLockedPresetsPerMode` |
| `tests/test_log_redact.py` | `tests/test_logs.py::TestLogRedact` |
| `tests/test_loss_streak.py` | `tests/test_signal_filters.py::TestLossStreak` |
| `tests/test_manual_order_close.py` | `tests/test_dashboard.py::TestTheUi` |
| `tests/test_max_profit_pct_levels.py` | `tests/test_signal_filters.py::TestMaxProfitPctLevels` |
| `tests/test_max_sl_clamp.py` | `tests/test_signal_filters.py::TestSell` |
| `tests/test_mean_reversion.py` | `tests/test_signal_filters.py::TestMeanReversion` |
| `tests/test_mirror_mode.py` | `tests/test_mirror_and_virtual_only.py::TestMirrorMode` |
| `tests/test_mode_manager.py` | `tests/test_config_and_modes.py::TestModeManager` |
| `tests/test_mode_switch.py` | `tests/test_config_and_modes.py::TestModeSwitch` |
| `tests/test_mr_engine.py` | `tests/test_signal_filters.py::TestMrEngine` |
| `tests/test_no_rank_change_eviction.py` | `tests/test_virtual_simulator.py::TestMaxAge` |
| `tests/test_no_silent_order_rejections.py` | `tests/test_order_executor.py::TestNoSilentOrderRejections` |
| `tests/test_notifier.py` | `tests/test_notifications.py::TestNotifier` |
| `tests/test_order_duration_and_real_widget.py` | `tests/test_dashboard.py::TestTheWidget` |
| `tests/test_order_executor.py` | `tests/test_order_executor.py::TestOrderExecutor` |
| `tests/test_order_executor_balance.py` | `tests/test_balance.py::TestOrderExecutorBalance` |
| `tests/test_order_executor_entry_fill.py` | `tests/test_order_executor.py::TestOrderExecutorEntryFill` |
| `tests/test_parent_alignment_gate.py` | `tests/test_signal_filters.py::TestParentAlignmentGate` |
| `tests/test_parity_part2.py` | `tests/test_virtual_simulator.py::TestVirtualRealParityPart2` |
| `tests/test_pending_sl_cancel.py` | `tests/test_order_executor.py::TestPendingSlCancel` |
| `tests/test_per_instance_paths.py` | `tests/test_config_and_modes.py::TestPerInstancePaths` |
| `tests/test_per_mode_risk_config.py` | `tests/test_config_and_modes.py::TestPerModeRiskConfig` |
| `tests/test_preset_substitution.py` | `tests/test_virtual_tracker.py::TestPresetSubstitution` |
| `tests/test_probe_waits_out_the_ban.py` | `tests/test_rate_limit.py::TestProbeWaitsOutTheBan` |
| `tests/test_rank1_not_open_while_real_position_open.py` | `tests/test_virtual_simulator.py::TestBehaviour` |
| `tests/test_rank1_virtual_pool.py` | `tests/test_virtual_simulator.py::TestOpening` |
| `tests/test_rate_limit_guard.py` | `tests/test_rate_limit.py::TestRateLimitGuard` |
| `tests/test_rate_limit_integration.py` | `tests/test_rate_limit.py::TestRateLimitIntegration` |
| `tests/test_rate_limit_probe_and_alerts.py` | `tests/test_rate_limit.py::TestRateLimitProbeAndAlerts` |
| `tests/test_rate_limit_staged_recovery.py` | `tests/test_rate_limit.py::TestRateLimitStagedRecovery` |
| `tests/test_real_order_recording.py` | `tests/test_order_executor.py::TestRealOrderRecording` |
| `tests/test_recalc_script_ranges.py` | `tests/test_config_and_modes.py::TestRecalcScriptRanges` |
| `tests/test_replay_api.py` | `tests/test_dashboard.py::TestReplayApi` |
| `tests/test_reweight_after_drag.py` | `tests/test_weights.py::TestDropAmongZerosDeactivates` |
| `tests/test_risk_config.py` | `tests/test_config_and_modes.py::TestRiskConfig` |
| `tests/test_risk_manager.py` | `tests/test_risk_manager.py::TestRiskManager` |
| `tests/test_risk_manager_peak_guard.py` | `tests/test_risk_manager.py::TestPeakBalanceGuard` |
| `tests/test_risk_manager_unknown_symbol.py` | `tests/test_risk_manager.py::TestRiskManagerUnknownSymbol` |
| `tests/test_running_balance_and_drift.py` | `tests/test_balance.py::TestRunningBalanceAndDrift` |
| `tests/test_safe_write.py` | `tests/test_logs.py::TestSafeWrite` |
| `tests/test_shared_settings.py` | `tests/test_config_and_modes.py::TestSharedSettings` |
| `tests/test_sl_clamp_optional.py` | `tests/test_signal_filters.py::TestSlClampOptional` |
| `tests/test_slippage.py` | `tests/test_order_executor.py::TestSlippage` |
| `tests/test_startup_api_calls.py` | `tests/test_startup_and_deploy.py::TestKlineGapSkip` |
| `tests/test_startup_backtest_optional.py` | `tests/test_startup_and_deploy.py::TestStartupBacktestOptional` |
| `tests/test_startup_seeds_balance_under_ban.py` | `tests/test_balance.py::TestStartupSeedsBalanceUnderBan` |
| `tests/test_symbol_discovery.py` | `tests/test_data_feed.py::TestSymbolDiscovery` |
| `tests/test_symbol_hot_subscribe.py` | `tests/test_data_feed.py::TestSymbolHotSubscribe` |
| `tests/test_symbol_registry_atomic.py` | `tests/test_symbol_registry.py::TestSymbolRegistryAtomic` |
| `tests/test_symbol_registry_disable.py` | `tests/test_symbol_registry.py::TestSymbolRegistryDisable` |
| `tests/test_symbol_registry_pause.py` | `tests/test_symbol_registry.py::TestSymbolRegistryPause` |
| `tests/test_symbol_registry_per_mode.py` | `tests/test_symbol_registry.py::TestSymbolRegistryPerMode` |
| `tests/test_system_log.py` | `tests/test_logs.py::TestSystemLog` |
| `tests/test_tats_sole_candidate_sizing.py` | `tests/test_risk_manager.py::TestTatsEdgeCases` |
| `tests/test_telegram_balance_sources.py` | `tests/test_balance.py::TestTelegramBalanceSources` |
| `tests/test_telegram_menu.py` | `tests/test_notifications.py::TestTelegramMenu` |
| `tests/test_telegram_views.py` | `tests/test_notifications.py::TestTelegramViews` |
| `tests/test_testnet_rest_host.py` | `tests/test_rate_limit.py::TestTestnetRestHost` |
| `tests/test_unconfigured_symbol_is_virtual_only.py` | `tests/test_symbol_registry.py::TestUnconfiguredSymbolIsVirtualOnly` |
| `tests/test_unused_allocation_is_reoffered.py` | `tests/test_risk_manager.py::TestTheMeasuredCandle` |
| `tests/test_virtual_only_control_resources.py` | `tests/test_mirror_and_virtual_only.py::TestVirtualOnlyControlResources` |
| `tests/test_virtual_only_needs_no_credentials.py` | `tests/test_mirror_and_virtual_only.py::TestVirtualOnlyNeedsNoCredentials` |
| `tests/test_virtual_only_no_telegram.py` | `tests/test_mirror_and_virtual_only.py::TestVirtualOnlyNoTelegram` |
| `tests/test_virtual_only_setting.py` | `tests/test_mirror_and_virtual_only.py::TestVirtualOnlySetting` |
| `tests/test_virtual_only_skips_real_orders.py` | `tests/test_mirror_and_virtual_only.py::TestVirtualOnlySkipsRealOrders` |
| `tests/test_virtual_order_simulator.py` | `tests/test_virtual_simulator.py::TestRankSimulator` |
| `tests/test_virtual_persistence.py` | `tests/test_virtual_simulator.py::TestVirtualPersistence` |
| `tests/test_virtual_pnl_charges_slippage.py` | `tests/test_virtual_simulator.py::TestTheChargeIsApplied` |
| `tests/test_virtual_tracker.py` | `tests/test_virtual_tracker.py::TestVirtualTracker` |
| `tests/test_virtual_tracker_helpers.py` | `tests/test_virtual_tracker.py::TestVirtualTrackerHelpers` |
| `tests/test_watchdog_rate_limit_guard.py` | `tests/test_rate_limit.py::TestWatchdogRateLimitGuard` |
| `tests/test_weight_audit.py` | `tests/test_weights.py::TestDetectingChanges` |
| `tests/test_weight_rebalancer.py` | `tests/test_weights.py::TestBlendWeights` |
| `tests/test_weight_shadow.py` | `tests/test_weights.py::TestWeightShadow` |
| `tests/test_winpct_excludes_bookkeeping.py` | `tests/test_dashboard.py::TestWinPctExcludesBookkeeping` |
| `tests/test_ws_kline_cache.py` | `tests/test_data_feed.py::TestWsKlineCache` |
| `tests/test_ws_kline_full_fields.py` | `tests/test_data_feed.py::TestWsKlineFullFields` |
