use serde::Deserialize;

/// Application configuration.
#[derive(Debug, Deserialize)]
pub struct AppConfig {
    pub debug: bool,
    pub port: u16,
    pub database_url: String,
}

impl AppConfig {
    /// Load configuration from a JSON string.
    pub fn from_json(json: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(json)
    }

    /// Return default development configuration.
    pub fn default_dev() -> Self {
        AppConfig {
            debug: true,
            port: 8080,
            database_url: "sqlite::memory:".to_string(),
        }
    }
}
