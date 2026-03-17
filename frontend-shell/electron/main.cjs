const { app, BrowserWindow } = require("electron");
const path = require("path");

const DEV_SERVER_URL = process.env.ELECTRON_RENDERER_URL || "http://127.0.0.1:3000";
const SHOULD_OPEN_DEVTOOLS = process.env.ELECTRON_OPEN_DEVTOOLS === "1";

function createWindow() {
  const window = new BrowserWindow({
    width: 1360,
    height: 920,
    minWidth: 1080,
    minHeight: 760,
    title: "Desktop Assistant Shell",
    backgroundColor: "#f4f0e8",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (!app.isPackaged) {
    loadDevServer(window);
    if (SHOULD_OPEN_DEVTOOLS) {
      window.webContents.openDevTools({ mode: "detach" });
    }
    return;
  }

  window.loadFile(path.join(__dirname, "..", "dist", "index.html"));
}

function loadDevServer(window, attempt = 0) {
  window.loadURL(DEV_SERVER_URL).catch(() => {
    if (attempt >= 20 || window.isDestroyed()) {
      return;
    }
    setTimeout(() => {
      loadDevServer(window, attempt + 1);
    }, 500);
  });
}

app.whenReady().then(() => {
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
