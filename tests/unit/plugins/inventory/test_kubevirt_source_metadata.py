# -*- coding: utf-8 -*-
# Apache License 2.0 (see LICENSE or http://www.apache.org/licenses/LICENSE-2.0)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import hashlib
import json

import pytest

from ansible_collections.kubevirt.core.plugins.inventory.kubevirt import (
    InventoryOptions,
    KubeVirtInventoryException,
    _load_source_metadata,
)


def write_collection(tmp_path, *, manifest=None, plugin_source=b"plugin source"):
    plugin = (
        tmp_path
        / "ansible_collections"
        / "kubevirt"
        / "core"
        / "plugins"
        / "inventory"
        / "kubevirt.py"
    )
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(plugin_source)
    manifest_path = plugin.parents[2] / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(
            manifest
            if manifest is not None
            else {
                "collection_info": {
                    "namespace": "kubevirt",
                    "name": "core",
                    "version": "2.3.0",
                }
            }
        ),
        encoding="utf-8",
    )
    return plugin


def test_source_metadata_reports_installed_identity_and_own_file_hash(tmp_path):
    source = b"installed inventory plugin fixture"
    plugin = write_collection(tmp_path, plugin_source=source)

    assert _load_source_metadata(str(plugin)) == {
        "collection_name": "kubevirt.core",
        "collection_version": "2.3.0",
        "plugin_sha256": hashlib.sha256(source).hexdigest(),
    }


@pytest.mark.parametrize(
    "manifest",
    [
        [],
        {},
        {"collection_info": []},
        {"collection_info": {"namespace": "other", "name": "core", "version": "2.3.0"}},
        {
            "collection_info": {
                "namespace": "kubevirt",
                "name": "core",
                "version": "2.3.0\n",
            }
        },
        {"collection_info": {"namespace": "kubevirt", "name": "core", "version": 2}},
    ],
)
def test_malformed_or_foreign_installed_metadata_fails_closed(tmp_path, manifest):
    plugin = write_collection(tmp_path, manifest=manifest)
    with pytest.raises(KubeVirtInventoryException):
        _load_source_metadata(str(plugin))


def test_missing_manifest_fails_closed(tmp_path):
    plugin = write_collection(tmp_path)
    (plugin.parents[2] / "MANIFEST.json").unlink()
    with pytest.raises(KubeVirtInventoryException):
        _load_source_metadata(str(plugin))


def test_metadata_is_attached_to_each_host_without_copying_environment(
    mocker, inventory, hosts, monkeypatch
):
    metadata = {
        "collection_name": "kubevirt.core",
        "collection_version": "2.3.0",
        "plugin_sha256": "a" * 64,
    }
    inventory._source_metadata = metadata
    monkeypatch.setenv("K8S_AUTH_PASSWORD", "SOURCE_METADATA_SENTINEL")
    for hostname, prefix in (("ns-first", "vm"), ("ns-second", "vmi")):
        inventory.inventory.add_host(hostname)
        inventory._set_common_vars(
            hostname,
            prefix,
            {"metadata": {"name": hostname}, "status": {}},
            InventoryOptions(),
        )

    for hostname in ("ns-first", "ns-second"):
        assert hosts[hostname]["kubevirt_inventory_source_metadata"] == metadata
        assert "SOURCE_METADATA_SENTINEL" not in repr(hosts[hostname])


def test_default_off_does_not_read_or_publish_source_metadata(
    mocker, inventory, monkeypatch
):
    from ansible.plugins.inventory import Cacheable
    from ansible_collections.kubevirt.core.plugins.inventory import kubevirt

    monkeypatch.delenv("KUBEVIRT_REPORT_SOURCE_METADATA", raising=False)
    mocker.patch.object(
        Cacheable, "cache", new_callable=mocker.PropertyMock, return_value={}
    )
    mocker.patch.object(inventory, "_read_config_data", return_value={})
    mocker.patch.object(inventory, "get_cache_key", return_value="key")
    mocker.patch.object(
        inventory,
        "get_option",
        side_effect=lambda name: False if name == "cache" else None,
    )
    mocker.patch.object(kubevirt, "HAS_K8S_MODULE_HELPER", True)
    load_metadata = mocker.patch.object(kubevirt, "_load_source_metadata")
    mocker.patch.object(
        inventory, "_get_api_client", return_value=(object(), None, None, None)
    )
    mocker.patch.object(
        inventory,
        "_fetch_objects",
        return_value={
            "cluster_domain": None,
            "default_hostname": "cluster",
            "namespaces": {},
        },
    )
    mocker.patch.object(inventory, "_populate_inventory")

    inventory.parse(None, None, "fixture.kubevirt.yml", False)

    load_metadata.assert_not_called()
    assert inventory._source_metadata is None


@pytest.mark.parametrize("source", ["config", "environment"])
def test_opt_in_loads_metadata_once_before_population(
    mocker, inventory, monkeypatch, source
):
    from ansible.plugins.inventory import Cacheable
    from ansible_collections.kubevirt.core.plugins.inventory import kubevirt

    monkeypatch.delenv("KUBEVIRT_REPORT_SOURCE_METADATA", raising=False)
    config = {"report_source_metadata": True} if source == "config" else {}
    if source == "environment":
        monkeypatch.setenv("KUBEVIRT_REPORT_SOURCE_METADATA", "true")
    mocker.patch.object(
        Cacheable, "cache", new_callable=mocker.PropertyMock, return_value={}
    )
    mocker.patch.object(inventory, "_read_config_data", return_value=config)
    mocker.patch.object(inventory, "get_cache_key", return_value="key")
    mocker.patch.object(
        inventory,
        "get_option",
        side_effect=lambda name: {"cache": False, "report_source_metadata": True}.get(
            name
        ),
    )
    mocker.patch.object(kubevirt, "HAS_K8S_MODULE_HELPER", True)
    metadata = {
        "collection_name": "kubevirt.core",
        "collection_version": "2.3.0",
        "plugin_sha256": "b" * 64,
    }
    load_metadata = mocker.patch.object(
        kubevirt, "_load_source_metadata", return_value=metadata
    )
    mocker.patch.object(
        inventory, "_get_api_client", return_value=(object(), None, None, None)
    )
    mocker.patch.object(
        inventory,
        "_fetch_objects",
        return_value={
            "cluster_domain": None,
            "default_hostname": "cluster",
            "namespaces": {},
        },
    )
    mocker.patch.object(inventory, "_populate_inventory")

    inventory.parse(None, None, "fixture.kubevirt.yml", False)

    load_metadata.assert_called_once_with()
    assert inventory._source_metadata == metadata
