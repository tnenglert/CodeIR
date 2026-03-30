use serde::Deserialize;

/// Application configuration.
#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub app_name: String,
    pub port: u16,
    pub debug: bool,
}

impl Config {
    pub fn load_config() -> Self {
        // In a real app this would read from a file or env vars
        Config {
            app_name: "sample-app".to_string(),
            port: 8080,
            debug: false,
        }
    }

    pub fn is_production(&self) -> bool {
        !self.debug
    }
}

impl Default for Config {
    fn default() -> Self {
        Config {
            app_name: "default".to_string(),
            port: 3000,
            debug: true,
        }
    }
}
