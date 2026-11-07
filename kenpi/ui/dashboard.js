const labels = {
  camera_control: "Camera control",
  live_preview: "Live preview",
  media_access: "Camera media",
  kenwood_touchscreen: "Kenwood touchscreen",
  vehicle_power_coordination: "Vehicle coordination"
};

async function refresh() {
  const status = document.querySelector("#status");
  try {
    const response = await fetch("/api/v1/capabilities", { cache: "no-store" });
    if (!response.ok) throw new Error("service unavailable");
    const body = await response.json();
    const section = document.querySelector("#capabilities");
    section.replaceChildren(...body.capabilities.map((item) => {
      const card = document.createElement("article");
      const title = document.createElement("h2");
      title.textContent = labels[item.name] || item.name;
      const state = document.createElement("div");
      state.className = `state ${item.state}`;
      state.textContent = item.state.replaceAll("_", " ");
      const detail = document.createElement("p");
      detail.textContent = `${item.detail} · evidence ${item.evidence}`;
      card.append(title, state, detail);
      return card;
    }));
    status.textContent = "Capability data is current.";
  } catch {
    status.textContent = "Kenpi status service is unavailable.";
  }
}

refresh();
window.setInterval(refresh, 10000);
