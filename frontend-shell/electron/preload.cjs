const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("desktopAssistantShell", {
  platform: process.platform,
});
