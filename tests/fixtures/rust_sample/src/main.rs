use clap::Parser;

mod config;
mod db;
mod errors;
mod handlers;
mod models;

use config::AppConfig;
use db::Database;

/// CLI arguments for the application
#[derive(Parser, Debug)]
#[command(name = "rust_sample")]
struct Args {
    /// Configuration file path
    #[arg(short, long, default_value = "config.toml")]
    config: String,

    /// Enable verbose logging
    #[arg(short, long)]
    verbose: bool,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let config = AppConfig::load(&args.config)?;
    let db = Database::connect(&config.database_url).await?;

    if args.verbose {
        println!("Connected to database");
    }

    run_server(config, db).await
}

async fn run_server(
    config: AppConfig,
    db: Database,
) -> Result<(), Box<dyn std::error::Error>> {
    let addr = format!("{}:{}", config.host, config.port);
    println!("Starting server on {}", addr);

    let user_handler = handlers::user::UserHandler::new(db.clone());
    let auth_handler = handlers::auth::AuthHandler::new(db, config.secret_key.clone());

    user_handler.list_users().await?;
    auth_handler.validate_token("test").await?;

    Ok(())
}
