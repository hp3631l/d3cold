"""Unit tests for dgpu-doctor remediation and helpers."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dgpu_doctor.pci import GPUDevice
from dgpu_doctor.remediate import fix_btop, install_prime_run


class TestRemediate(unittest.TestCase):

    def test_gpu_device_suspended_logic(self):
        gpu = GPUDevice(
            pci_address="0000:01:00.0",
            vendor_id="0x10de",
            device_id="0x28e0",
            class_id="0x030000",
            is_nvidia=True,
            is_primary=False,
            sysfs_path=Path("/fake/path"),
        )
        with patch.object(GPUDevice, "runtime_status", "suspended"):
            with patch.object(GPUDevice, "power_state", "D3cold"):
                self.assertTrue(gpu.is_suspended)

        with patch.object(GPUDevice, "runtime_status", "active"):
            with patch.object(GPUDevice, "power_state", "D0"):
                self.assertFalse(gpu.is_suspended)

    @patch("pathlib.Path.home")
    def test_fix_btop_missing_conf(self, mock_home):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            ok, msg = fix_btop()
            self.assertFalse(ok)
            self.assertIn("does not exist", msg)

    @patch("pathlib.Path.home")
    def test_fix_btop_modifies_conf(self, mock_home):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            mock_home.return_value = base
            conf_dir = base / ".config/btop"
            conf_dir.mkdir(parents=True)
            conf_file = conf_dir / "btop.conf"
            conf_file.write_text('show_gpu_info = "Auto"\nnvml_measure_pcie_speeds = true\n')

            ok, msg = fix_btop()
            self.assertTrue(ok)
            self.assertIn("Optimized", msg)
            content = conf_file.read_text()
            self.assertIn('show_gpu_info = "Off"', content)
            self.assertIn("nvml_measure_pcie_speeds = false", content)

    @patch("pathlib.Path.home")
    def test_install_prime_run(self, mock_home):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            ok, msg = install_prime_run()
            self.assertTrue(ok)
            runner = Path(tmpdir) / ".local/bin/prime-run"
            self.assertTrue(runner.exists())
            self.assertIn("__NV_PRIME_RENDER_OFFLOAD=1", runner.read_text())


if __name__ == "__main__":
    unittest.main()
