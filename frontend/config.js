// Static dashboard API base configuration.
// Empty string = same-origin (local FastAPI server at http://127.0.0.1:8000/ui/).
// For Vercel static hosting, set this to the central ve-jit backend origin, e.g.:
//   window.VEJIT_API_BASE = "https://vejit-api.example.com";
// It can also be injected by a deployment step without touching app.js.
window.VEJIT_API_BASE = "";
