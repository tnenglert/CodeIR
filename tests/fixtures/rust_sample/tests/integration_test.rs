use rust_sample::models::{User, UserRole, JsonSerializable};
use rust_sample::config::AppConfig;

#[test]
fn test_user_json_roundtrip() {
    let user = User::new(1, "alice".into(), "alice@example.com".into());
    let json = user.to_json().unwrap();
    let restored = User::from_json(&json).unwrap();
    assert_eq!(restored.username, "alice");
}

#[test]
fn test_config_defaults() {
    let config = AppConfig::default();
    assert_eq!(config.port, 8080);
    assert_eq!(config.host, "127.0.0.1");
}

#[test]
fn test_user_promote_to_admin() {
    let mut user = User::new(1, "bob".into(), "bob@example.com".into());
    assert_eq!(user.role, UserRole::Viewer);
    user.promote(UserRole::Admin).unwrap();
    assert!(user.is_admin());
}
