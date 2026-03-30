use std::fmt;

/// Application-level error type.
#[derive(Debug, Clone)]
pub enum AppError {
    NotFound(String),
    Validation(String),
    Internal(String),
    Unauthorized,
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::NotFound(msg) => write!(f, "not found: {}", msg),
            AppError::Validation(msg) => write!(f, "validation error: {}", msg),
            AppError::Internal(msg) => write!(f, "internal error: {}", msg),
            AppError::Unauthorized => write!(f, "unauthorized"),
        }
    }
}

impl std::error::Error for AppError {}

impl From<std::io::Error> for AppError {
    fn from(err: std::io::Error) -> Self {
        AppError::Internal(err.to_string())
    }
}
