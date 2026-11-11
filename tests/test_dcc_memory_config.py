from deerflow.config.app_config import AppConfig
from dcc_harness.datasets.benchmark_plugins import configure_dataset_app, resolve_dataset_plugin


def test_dcc_benchmark_disables_long_term_memory_after_plugin_configuration():
    app = AppConfig.from_file('configs/stackplanner_benchmarks.yaml')
    plugin = resolve_dataset_plugin('dcc_inline', ('dcc_harness.preprocessing.native:make_inline_plugin',))
    configured = configure_dataset_app(app, plugin)
    assert configured.memory.enabled is False
    assert configured.memory.injection_enabled is False
