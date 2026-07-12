from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from parser.external_package_models import CONTRACT_NAME, CONTRACT_VERSION, MANIFEST_VERSION, PACKAGE_OPEN_PROFILE_ID, PackageOpenReason, sha256_identity
from parser.external_package_reader import MANIFEST_NAME, MAX_MANIFEST_BYTES, PackageReadError, read_directory_package


def package_manifest() -> dict[str, object]:
    artifact = {"artifact_id":"trace","artifact_kind":"trace","relative_path":"trace.btf","required":True,"bytes":1,"sha256":"a"*64,"media_type":"application/octet-stream","source_artifact_id":"btf_1core","content_version":"p7.2-source-inventory-v1","provenance_reference":"freertos_btf_trace:btf_1core"}
    artifact["artifact_identity"] = sha256_identity(artifact)
    source = {"source_id":"freertos_btf_trace","source_kind":"public_pre_generated_trace","repository":"https://example.invalid/source","repository_commit":"a"*40,"source_path":"tracedata/example.btf","source_artifact_id":"btf_1core","source_checksum":"a"*64,"source_bytes":1,"rtos_name":"FreeRTOS","trace_format":"BTF","source_format_version":"p7.2-source-inventory-v1","generation_mode":"pre_generated_repository_sample","hardware_validation":False,"license_spdx":"MIT","acquisition_status":"acquired"}
    source["source_identity"] = sha256_identity({"source_kind":source["source_kind"],"source_trace_id":source["source_artifact_id"],"source_trace_checksum":source["source_checksum"],"source_trace_bytes":source["source_bytes"],"source_format":source["trace_format"],"source_format_version":source["source_format_version"],"acquisition_status":source["acquisition_status"]})
    value = {"contract_name":CONTRACT_NAME,"contract_version":CONTRACT_VERSION,"manifest_version":MANIFEST_VERSION,"package_kind":"self_contained","package_identity":"0"*64,"source":source,"artifacts":[artifact],"external_references":[],"configuration":{"validation_mode":"offline","package_profile":"p7.3-directory-v1"},"configuration_identity":sha256_identity({"validation_mode":"offline","package_profile":"p7.3-directory-v1"}),"package_open_profile_id":PACKAGE_OPEN_PROFILE_ID,"checksum_algorithm":"sha256","hardware_validation":False,"limitations":["No semantic replay."]}
    value["package_identity"] = sha256_identity({"manifest_name":MANIFEST_NAME,"manifest":{key:item for key,item in value.items() if key != "package_identity"}})
    return value


class DirectoryPackageReaderTests(unittest.TestCase):
    def make_package(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "trace.btf").write_bytes(b"x")
        (root / MANIFEST_NAME).write_text(json.dumps(package_manifest()), encoding="utf-8")
        return temp, root

    def test_reads_directory_without_artifact_validation(self) -> None:
        temp, root = self.make_package()
        with temp:
            read = read_directory_package(root)
            self.assertEqual(read.manifest.contract_version, CONTRACT_VERSION)
            self.assertEqual(len(read.manifest_sha256), 64)

    def test_missing_bad_json_utf8_and_size_are_rejected(self) -> None:
        for content, reason in ((None, PackageOpenReason.MANIFEST_MISSING), (b"{", PackageOpenReason.MANIFEST_INVALID), (b"\xff", PackageOpenReason.MANIFEST_UNREADABLE), (b" " * (MAX_MANIFEST_BYTES + 1), PackageOpenReason.MANIFEST_SIZE_LIMIT)):
            with tempfile.TemporaryDirectory() as name, self.subTest(reason=reason):
                root = Path(name)
                if content is not None: (root / MANIFEST_NAME).write_bytes(content)
                with self.assertRaises(PackageReadError) as caught: read_directory_package(root)
                self.assertEqual(caught.exception.reason, reason)

    def test_contract_path_and_archive_errors(self) -> None:
        temp, root = self.make_package()
        with temp:
            value = package_manifest(); value["contract_version"] = "other"
            (root / MANIFEST_NAME).write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(PackageReadError) as caught: read_directory_package(root)
            self.assertEqual(caught.exception.reason, PackageOpenReason.CONTRACT_VERSION_UNSUPPORTED)
        with tempfile.TemporaryDirectory() as name:
            archive = Path(name) / "package.zip"; archive.write_bytes(b"not read")
            with self.assertRaises(PackageReadError) as caught: read_directory_package(archive)
            self.assertEqual(caught.exception.reason, PackageOpenReason.ARCHIVE_UNSUPPORTED)

    def test_root_manifest_and_intermediate_symlinks_are_rejected(self) -> None:
        temp, root = self.make_package()
        with temp:
            link = Path(f"{root}-link"); link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(PackageReadError) as caught: read_directory_package(link)
            self.assertEqual(caught.exception.reason, PackageOpenReason.SYMLINK_FORBIDDEN)
            (root / MANIFEST_NAME).unlink(); (root / MANIFEST_NAME).symlink_to("trace.btf")
            with self.assertRaises(PackageReadError) as caught: read_directory_package(root)
            self.assertEqual(caught.exception.reason, PackageOpenReason.SYMLINK_FORBIDDEN)
        temp, root = self.make_package()
        with temp:
            (root / "trace.btf").unlink(); outside = Path(f"{root}-outside"); outside.mkdir(); (outside / "trace.btf").write_bytes(b"x"); (root / "nested").symlink_to(outside, target_is_directory=True)
            value = package_manifest(); value["artifacts"][0]["relative_path"] = "nested/trace.btf"; (root / MANIFEST_NAME).write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(PackageReadError) as caught: read_directory_package(root)
            self.assertEqual(caught.exception.reason, PackageOpenReason.SYMLINK_FORBIDDEN)

    def test_path_depth_limits_and_no_side_effect_apis(self) -> None:
        for invalid, reason in (("../trace",PackageOpenReason.PATH_TRAVERSAL),("/trace",PackageOpenReason.ABSOLUTE_PATH),("C:/trace",PackageOpenReason.ABSOLUTE_PATH),("a\\b",PackageOpenReason.PATH_NORMALIZATION)):
            temp, root = self.make_package()
            with temp, self.subTest(path=invalid):
                value = package_manifest(); value["artifacts"][0]["relative_path"] = invalid; (root / MANIFEST_NAME).write_text(json.dumps(value),encoding="utf-8")
                with self.assertRaises(PackageReadError) as caught: read_directory_package(root)
                self.assertEqual(caught.exception.reason,reason)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); deep: object = 0
            for _ in range(34): deep = [deep]
            (root / MANIFEST_NAME).write_text(json.dumps(deep),encoding="utf-8")
            with self.assertRaises(PackageReadError): read_directory_package(root)
        temp, root = self.make_package()
        with temp, mock.patch("subprocess.run", side_effect=AssertionError), mock.patch("os.getenv", side_effect=AssertionError):
            self.assertEqual(read_directory_package(root).root, root)

    def test_special_hardlink_and_depth_are_rejected(self) -> None:
        temp, root = self.make_package()
        with temp:
            hard = root / "hard.btf"; hard.hardlink_to(root / "trace.btf")
            with self.assertRaises(PackageReadError) as caught: read_directory_package(root)
            self.assertEqual(caught.exception.reason, PackageOpenReason.HARDLINK_FORBIDDEN)
        temp, root = self.make_package()
        with temp:
            nested = root
            for index in range(34): nested = nested / f"d{index}"; nested.mkdir()
            with self.assertRaises(PackageReadError) as caught: read_directory_package(root)
            self.assertEqual(caught.exception.reason, PackageOpenReason.ARTIFACT_COUNT_LIMIT)

    def test_duplicate_json_keys_and_ancestor_symlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); (root / MANIFEST_NAME).write_text('{"x":1,"x":2}', encoding="utf-8")
            with self.assertRaises(PackageReadError) as caught: read_directory_package(root)
            self.assertEqual(caught.exception.reason, PackageOpenReason.MANIFEST_INVALID)
        with tempfile.TemporaryDirectory() as name:
            real = Path(name) / "real"; real.mkdir(); (real / "trace.btf").write_bytes(b"x"); (real / MANIFEST_NAME).write_text(json.dumps(package_manifest()), encoding="utf-8")
            alias = Path(name) / "alias"; alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(PackageReadError) as caught: read_directory_package(alias)
            self.assertEqual(caught.exception.reason, PackageOpenReason.SYMLINK_FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
