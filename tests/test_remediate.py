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
    @patch("dgpu_doctor.pci.find_igpu")
    def test_fix_btop_amd_igpu(self, mock_igpu, mock_home):
        from dgpu_doctor.pci import GPUDevice
        mock_igpu.return_value = GPUDevice("0000:c3:00.0", "0x1002", "0x164c", "0x030000", False, True, Path("/fake"))
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            mock_home.return_value = base
            conf_dir = base / ".config/btop"
            conf_dir.mkdir(parents=True)
            conf_file = conf_dir / "btop.conf"
            conf_file.write_text('shown_gpus = "auto"\n')

            ok, msg = fix_btop()
            self.assertTrue(ok)
            content = conf_file.read_text()
            self.assertIn('shown_gpus = "amd"', content)

    @patch("subprocess.run")
    def test_fix_persistence_mode(self, mock_run):
        from dgpu_doctor.remediate import fix_persistence_mode
        mock_run.return_value = MagicMock(returncode=0, stdout="active\n")
        ok, msg = fix_persistence_mode()
        self.assertTrue(ok)
        self.assertIn("nvidia-persistenced", msg)

    @patch("pathlib.Path.home")
    def test_install_prime_run(self, mock_home):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            ok, msg = install_prime_run()
            self.assertTrue(ok)
            runner = Path(tmpdir) / ".local/bin/prime-run"
            self.assertTrue(runner.exists())
            self.assertIn("__NV_PRIME_RENDER_OFFLOAD=1", runner.read_text())

    @patch("dgpu_doctor.pci.get_nvidia_client_processes")
    @patch("os.kill")
    def test_kill_nvidia_clients_skips_compositors(self, mock_kill, mock_clients):
        from dgpu_doctor.remediate import kill_nvidia_clients
        mock_clients.return_value = [
            {"pid": 1344, "comm": "Hyprland", "cmdline": "Hyprland", "target": "/dev/nvidia0"},
            {"pid": 9999, "comm": "some_app", "cmdline": "some_app", "target": "/dev/nvidia0"},
        ]
        results = kill_nvidia_clients(force=True)
        # Verify Hyprland (1344) was NOT killed, but some_app (9999) was killed
        killed_pids = [r[0] for r in results]
        self.assertNotIn(1344, killed_pids)
        self.assertIn(9999, killed_pids)
        mock_kill.assert_any_call(9999, unittest.mock.ANY)


if __name__ == "__main__":
    unittest.main()
