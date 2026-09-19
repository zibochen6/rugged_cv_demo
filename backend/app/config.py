"""Runtime configuration for the Visual Hub server.

Camera sources, ports and the recording root come from the environment (systemd
EnvironmentFile, see backend/app/hub/config.py). This module only carries the
MJPEG preview stream and HTTP server defaults.
"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class StreamConfig:
    """MJPEG preview stream configuration."""
    preview_width: int = 960
    preview_height: int = 540
    jpeg_quality: int = 75
    target_fps: int = 30
    capture_resolution: List[int] = None
    client_buffer_size: int = 1
    drop_policy: str = "latest"

    def __post_init__(self):
        if self.capture_resolution is None:
            self.capture_resolution = [1280, 720]


@dataclass
class ServerConfig:
    """HTTP server configuration."""
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class AppConfig:
    """Application configuration."""
    stream: StreamConfig
    server: ServerConfig

    @classmethod
    def default(cls) -> "AppConfig":
        """Create the default configuration."""
        return cls(stream=StreamConfig(), server=ServerConfig())


# Global config instance
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = AppConfig.default()
    return _config