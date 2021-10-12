# (C) Datadog, Inc. 2019-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import pytest

from .common import CLICKHOUSE_VERSION
from .metrics import get_metrics

pytestmark = pytest.mark.e2e


def test_check(dd_agent_check, instance):
    # We do not do aggregator.assert_all_metrics_covered() because depending on timing, some other metrics may appear
    aggregator = dd_agent_check(instance, rate=True)
    server_tag = 'server:{}'.format(instance['server'])
    port_tag = 'port:{}'.format(instance['port'])
    metrics = get_metrics(CLICKHOUSE_VERSION)

    # assert at least 0 for clickhouse.dictionary.* because these metrics do not emit consistently in v21
    for metric in metrics:
        if CLICKHOUSE_VERSION == '21' and metric.startswith('clickhouse.dictionary.'):
            at_least = 0
        else:
            at_least = 1
        aggregator.assert_metric_has_tag(metric, server_tag, at_least=at_least)
        aggregator.assert_metric_has_tag(metric, port_tag, at_least=at_least)
        aggregator.assert_metric_has_tag(metric, 'db:default', at_least=at_least)
        aggregator.assert_metric_has_tag(metric, 'foo:bar', at_least=at_least)
    aggregator.assert_metric('clickhouse.table.replicated.total')

    if CLICKHOUSE_VERSION == '21':
        at_least = 0
        aggregator.assert_metric(
            'clickhouse.dictionary.item.current',
            tags=[server_tag, port_tag, 'db:default', 'foo:bar', 'dictionary:test'],
            at_least=at_least,
        )
    else:
        at_least = 1
        aggregator.assert_metric(
            'clickhouse.dictionary.item.current',
            tags=[server_tag, port_tag, 'db:default', 'foo:bar', 'dictionary:test'],
            at_least=at_least,
        )