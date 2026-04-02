use std::fmt;

/// Application-level error types.
#[derive(Debug)]
pub enum AppError {
    /// Configuration file errors
    Config(String),
    /// Database connection or query errors
    Database(String),
    /// Authentication failures
    Auth(String),
    /// Resource not found
    NotFound(String),
    /// Internal server error
    Internal(String),
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::Config(msg) => write!(f, "Config error: {}", msg),
            AppError::Database(msg) => write!(f, "Database error: {}", msg),
            AppError::Auth(msg) => write!(f, "Auth error: {}", msg),
            AppError::NotFound(msg) => write!(f, "Not found: {}", msg),
            AppError::Internal(msg) => write!(f, "Internal error: {}", msg),
        }
    }
}

impl std::error::Error for AppError {}

impl From<std::io::Error> for AppError {
    fn from(err: std::io::Error) -> Self {
        AppError::Internal(err.to_string())
    }
}

impl From<serde_json::Error> for AppError {
    fn from(err: serde_json::Error) -> Self {
        AppError::Internal(format!("JSON error: {}", err))
    }
}
