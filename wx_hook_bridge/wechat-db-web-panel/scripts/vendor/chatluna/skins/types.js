// WeChat adaptation of upstream avatar lookup. Rendering stays local.
const fallback = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="32" fill="#ded5ed"/><circle cx="32" cy="24" r="11" fill="#695582"/><path d="M12 60v-6a20 20 0 0 1 40 0v6" fill="#695582"/></svg>').toString('base64');
let avatars = {};
exports.setAvatars = values => { avatars = values; };
exports.getAvatarUrl = id => avatars[String(id)] || `data:image/svg+xml;base64,${fallback}`;
