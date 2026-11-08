"""Unit tests for dgpu-doctor audit and pci discovery."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dgpu_doctor.audit import run_audit
from dgpu_doctor.pci import GPUDevice


class TestDGPUAudit(unittest.TestCase):

    @patch("dgpu_doctor.audit.find_nvidia_dgpu")
    @patch("dgpu_doctor.audit.find_nvidia_audio")
    @patch("dgpu_doctor.audit.get_nvidia_client_processes")
    def test_clean_suspended_state(self, mock_clients, mock_audio, mock_dgpu):
        # Setup mock suspended dGPU
        mock_gpu = MagicMock(spec=GPUDevice)
        mock_gpu.name = "NVIDIA GeForce RTX 4050 Laptop GPU"
        mock_gpu.pci_address = "0000:01:00.0"
        mock_gpu.runtime_status = "suspended"
        mock_gpu.power_control = "auto"
        mock_gpu.sysfs_path = Path("/sys/bus/pci/devices/0000:01:00.0")

        mock_dgpu.return_value = mock_gpu
        mock_audio.return_value = None  # No audio device holding it
        mock_clients.return_value = []   # No open clients

        report = run_audit()
        self.assertEqual(report.runtime_status, "suspended")
        self.assertEqual(report.power_control, "auto")

        # Verify findings
        pm_finding = next((f for f in report.findings if f.category == "Runtime PM" and "deep sleep" in f.title), None)
        self.assertIsNotNone(pm_finding)
        self.assertEqual(pm_finding.status, "PASS")

    @patch("dgpu_doctor.audit.find_nvidia_dgpu")
    @patch("dgpu_doctor.audit.find_nvidia_audio")
    @patch("dgpu_doctor.audit.get_nvidia_client_processes")
    def test_stuck_active_with_audio_leak(self, mock_clients, mock_audio, mock_dgpu):
        mock_gpu = MagicMock(spec=GPUDevice)
        mock_gpu.name = "NVIDIA GeForce RTX 4050 Laptop GPU"
        mock_gpu.pci_address = "0000:01:00.0"
        mock_gpu.runtime_status = "active"
        mock_gpu.power_control = "on"
        mock_gpu.sysfs_path = Path("/sys/bus/pci/devices/0000:01:00.0")

        mock_dgpu.return_value = mock_gpu

        # Simulate audio device bound to snd_hda_intel
        mock_audio_path = MagicMock(spec=Path)
        mock_audio_path.exists.return_value = True
        mock_audio_path.name = "0000:01:00.1"
        mock_driver = MagicMock(spec=Path)
        mock_driver.exists.return_value = True
        mock_driver.resolve.return_value.name = "snd_hda_intel"
        mock_audio_path.__truediv__.return_value = mock_driver

        mock_audio.return_value = mock_audio_path
        mock_clients.return_value = []

        report = run_audit()
        self.assertEqual(report.runtime_status, "active")
        self.assertTrue(report.has_failures)

        fail_audio = next((f for f in report.findings if f.category == "Audio Controller" and f.status == "FAIL"), None)
        self.assertIsNotNone(fail_audio)


if __name__ == "__main__":
    unittest.main()
