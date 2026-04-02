use serde::Deserialize;
use std::fs;
use std::path::Path;

use crate::errors::AppError;

/// Application configuration loaded from a TOML file.
#[derive(Debug, Clone, Deserialize)]
pub struct AppConfig {
    pub host: String,
    pub port: u16,
    pub database_url: String,
    pub secret_key: String,
    pub max_connections: u32,
}

impl AppConfig {
    /// Load configuration from a file path.
    pub fn load(path: &str) -> Result<Self, AppError> {
        let content = fs::read_to_string(Path::new(path))
            .map_err(|e| AppError::Config(format!("Failed to read config: {}", e)))?;
        let config: AppConfig = toml::from_str(&content)
            .map_err(|e| AppError::Config(format!("Failed to parse config: {}", e)))?;
        config.validate()?;
        Ok(config)
    }

    /// Validate configuration values.
    fn validate(&self) -> Result<(), AppError> {
        if self.port == 0 {
            return Err(AppError::Config("Port cannot be 0".into()));
        }
        if self.max_connections == 0 {
            return Err(AppError::Config("max_connections must be > 0".into()));
        }
        if self.secret_key.len() < 16 {
            return Err(AppError::Config("secret_key must be at least 16 chars".into()));
        }
        Ok(())
    }

    /// Return the full database connection string with pool settings.
    pub fn database_url_with_pool(&self) -> String {
        format!("{}?max_connections={}", self.database_url, self.max_connections)
    }
}

const DEFAULT_HOST: &str = "127.0.0.1";
const DEFAULT_PORT: u16 = 8080;

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            host: DEFAULT_HOST.to_string(),
            port: DEFAULT_PORT,
            database_url: "sqlite::memory:".to_string(),
            secret_key: "default-secret-key-change-me!!".to_string(),
            max_connections: 5,
        }
    }
}
