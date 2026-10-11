// ShareL browser extension: context menu items that send ShareX's NativeMessagingInput to
// the native host (sharel-native-host), which forwards them to the running ShareL.
const HOST = "app.sharel.sharel";
const api = typeof browser !== "undefined" ? browser : chrome;

const ITEMS = [
  {id: "UploadImage", title: "Upload image with ShareL", contexts: ["image"]},
  {id: "UploadVideo", title: "Upload video with ShareL", contexts: ["video"]},
  {id: "UploadAudio", title: "Upload audio with ShareL", contexts: ["audio"]},
  {id: "UploadText", title: "Upload text with ShareL", contexts: ["selection"]},
  {id: "ShortenURL", title: "Shorten URL with ShareL", contexts: ["link", "page"]},
];

function install() {
  api.contextMenus.removeAll(() => {
    for (const item of ITEMS)
      api.contextMenus.create(item);
  });
}

api.runtime.onInstalled.addListener(install);
api.runtime.onStartup.addListener(install);

// Exported for tests: the message the host receives for a context menu click.
function messageFor(info, tab) {
  switch (info.menuItemId) {
  case "UploadImage":
  case "UploadVideo":
  case "UploadAudio":
    return {Action: info.menuItemId, URL: info.srcUrl || ""};
  case "UploadText":
    return {Action: "UploadText", Text: info.selectionText || ""};
  case "ShortenURL":
    return {Action: "ShortenURL", URL: info.linkUrl || info.pageUrl || (tab && tab.url) || ""};
  default:
    return null;
  }
}

api.contextMenus.onClicked.addListener((info, tab) => {
  const msg = messageFor(info, tab);
  if (msg)
    api.runtime.sendNativeMessage(HOST, msg);
});

if (typeof module !== "undefined")
  module.exports = {messageFor, ITEMS, HOST};
