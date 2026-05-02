#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    net::TcpStream,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::Duration,
};

use tauri::Manager;

const BACKEND_HOST: &str = "localhost";
const BACKEND_PORT: u16 = 3978;

struct BackendProcess(Mutex<Option<Child>>);

struct BackendLaunch {
    command: PathBuf,
    args: Vec<String>,
    current_dir: PathBuf,
    hands_command: Option<PathBuf>,
    runtime_dir: PathBuf,
    log_dir: PathBuf,
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        let Ok(mut child_guard) = self.0.lock() else {
            return;
        };
        let Some(mut child) = child_guard.take() else {
            return;
        };

        if let Ok(None) = child.try_wait() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn backend_is_running() -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(
        &format!("{BACKEND_HOST}:{BACKEND_PORT}")
            .parse()
            .unwrap_or_else(|_| ([127, 0, 0, 1], BACKEND_PORT).into()),
        Duration::from_millis(300),
    ) else {
        return false;
    };

    let _ = stream.set_read_timeout(Some(Duration::from_millis(700)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(700)));
    let request = format!(
        "GET /health HTTP/1.1\r\nHost: {BACKEND_HOST}:{BACKEND_PORT}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }
    response.starts_with("HTTP/1.1 200") && response.contains("\"status\": \"ok\"")
}

fn repository_root() -> Option<PathBuf> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir.parent()?.parent().map(Path::to_path_buf)
}

fn python_interpreter(repo_root: &Path) -> PathBuf {
    let venv_python = if cfg!(target_os = "windows") {
        repo_root.join(".venv").join("Scripts").join("python.exe")
    } else {
        repo_root.join(".venv").join("bin").join("python")
    };

    if venv_python.exists() {
        return venv_python;
    }

    if cfg!(target_os = "windows") {
        PathBuf::from("python")
    } else {
        PathBuf::from("python3")
    }
}

fn append_log(path: &Path) -> Option<Stdio> {
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .ok()
        .map(Stdio::from)
}

fn executable_name(name: &str) -> String {
    if cfg!(target_os = "windows") {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

fn packaged_backend_launch(app: &tauri::App) -> Result<Option<BackendLaunch>, String> {
    let Ok(resource_dir) = app.path().resource_dir() else {
        return Ok(None);
    };

    let backend_root = resource_dir.join("backend");
    if !cfg!(debug_assertions) && !backend_root.exists() {
        return Err(format!(
            "packaged backend resources are missing at {}",
            backend_root.display()
        ));
    }

    let backend_exe = backend_root
        .join("azul-backend")
        .join(executable_name("azul-backend"));

    if !backend_exe.exists() {
        if !cfg!(debug_assertions) {
            return Err(format!(
                "packaged backend executable is missing at {}",
                backend_exe.display()
            ));
        }
        return Ok(None);
    }

    let hands_exe = backend_root
        .join("azul-hands-mcp")
        .join(executable_name("azul-hands-mcp"));
    if !hands_exe.exists() {
        return Err(format!(
            "packaged backend found, but MCP executable is missing at {}",
            hands_exe.display()
        ));
    }

    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("could not resolve AzulClaw app data directory: {error}"))?;
    let runtime_dir = app_data_dir.join("runtime");
    let log_dir = app_data_dir.join("logs");

    let current_dir = backend_exe
        .parent()
        .ok_or("could not resolve packaged backend directory")?
        .to_path_buf();

    Ok(Some(BackendLaunch {
        command: backend_exe,
        args: Vec::new(),
        current_dir,
        hands_command: Some(hands_exe),
        runtime_dir,
        log_dir,
    }))
}

fn development_backend_launch() -> Result<BackendLaunch, String> {
    let repo_root = repository_root().ok_or("could not resolve repository root")?;
    Ok(BackendLaunch {
        command: python_interpreter(&repo_root),
        args: vec![
            "-m".to_string(),
            "azul_backend.azul_brain.main_launcher".to_string(),
        ],
        current_dir: repo_root.clone(),
        hands_command: None,
        runtime_dir: repo_root.join("memory"),
        log_dir: repo_root.join("memory").join("runtime-logs"),
    })
}

fn backend_launch(app: &tauri::App) -> Result<BackendLaunch, String> {
    if cfg!(debug_assertions) {
        return development_backend_launch();
    }
    if let Some(launch) = packaged_backend_launch(app)? {
        return Ok(launch);
    }
    development_backend_launch()
}

fn start_backend(app: &tauri::App) -> Result<Option<Child>, String> {
    if backend_is_running() {
        eprintln!(
            "AzulClaw backend already reachable on http://localhost:{BACKEND_PORT}; reusing it."
        );
        return Ok(None);
    }

    let launch = backend_launch(app)?;
    fs::create_dir_all(&launch.log_dir)
        .map_err(|error| format!("could not create backend log directory: {error}"))?;
    fs::create_dir_all(&launch.runtime_dir)
        .map_err(|error| format!("could not create backend runtime directory: {error}"))?;

    let mut command = Command::new(&launch.command);
    command
        .args(&launch.args)
        .current_dir(&launch.current_dir)
        .env("PORT", BACKEND_PORT.to_string())
        .env("AZUL_RUNTIME_DIR", &launch.runtime_dir)
        .env("AZUL_BACKEND_LOG_DIR", &launch.log_dir)
        .stdout(
            append_log(&launch.log_dir.join("desktop-backend.out.log")).unwrap_or(Stdio::null()),
        )
        .stderr(
            append_log(&launch.log_dir.join("desktop-backend.err.log")).unwrap_or(Stdio::null()),
        );

    if let Some(hands_command) = &launch.hands_command {
        command.env("AZUL_HANDS_MCP_COMMAND", hands_command);
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    command
        .spawn()
        .map(Some)
        .map_err(|error| format!("could not start AzulClaw backend: {error}"))
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            match start_backend(app) {
                Ok(child) => {
                    app.manage(BackendProcess(Mutex::new(child)));
                }
                Err(error) => {
                    eprintln!("AzulClaw backend autostart failed: {error}");
                    app.manage(BackendProcess(Mutex::new(None)));
                }
            }
            Ok(())
        })
        .plugin(tauri_plugin_dialog::init())
        .run(tauri::generate_context!())
        .expect("error while running AzulClaw desktop");
}
