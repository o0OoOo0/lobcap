"""Optional VPIN feature provider; the accepted core v3 registry is unchanged."""
from dataclasses import replace
from functools import partial

from lobcap.backtest.vpin import NAME, VERSION, VolumeBuckets, VpinSettings
from .features import CausalFeatureEngine
from .signal_contracts import FeaturePoint


def settings_from_config(config):
    return VpinSettings(config.vpin_bucket_volume, config.vpin_bucket_count,
                        config.vpin_max_age_ms*1_000_000)


class VpinFeatureEngine(CausalFeatureEngine):
    def __init__(self, *args, settings=VpinSettings(), **kwargs):
        super().__init__(*args, **kwargs)
        self.vpin = VolumeBuckets(settings)

    def observe(self, event, client_available_time_ns):
        super().observe(event, client_available_time_ns)
        if event.trade is not None:
            self.vpin.observe(event.trade.size, event.trade.maker_side, event.time_ns,
                event.receive_ns, client_available_time_ns, event.source_refs)

    def observation(self, analysis_time_ns, acquisition_segment, feature_names=None):
        requested = (NAME,) if feature_names is None else tuple(feature_names)
        if NAME not in requested:
            raise ValueError("VPIN provider requires the VPIN projection")
        core = tuple(name for name in requested if name != NAME)
        base = super().observation(analysis_time_ns, acquisition_segment, core or ("mid",))
        state = self.vpin.snapshot(analysis_time_ns)
        point = FeaturePoint(NAME, VERSION, state["value"], "dimensionless", state["status"],
            analysis_time_ns, state["available_ns"] if state["available_ns"] is not None else self.start_client_ns,
            state["source_exchange_ns"], state["source_receive_ns"], state["source_refs"],
            state["oldest_retained_completion_ns"],
            analysis_time_ns-state["oldest_retained_completion_ns"] if state["oldest_retained_completion_ns"] is not None else None)
        points = (() if not core else base.points) + (point,)
        return replace(base, points=points)


def provider(config):
    return partial(VpinFeatureEngine, settings=settings_from_config(config))
