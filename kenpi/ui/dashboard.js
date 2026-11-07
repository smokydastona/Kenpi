const labels = {
  camera_control: "Camera control",
  live_preview: "Live preview",
  media_access: "Camera media",
  kenwood_touchscreen: "Kenwood touchscreen",
  vehicle_power_coordination: "Vehicle coordination"
};

const healthFields = {
  application_status: "Application",
  collection_status: "Health collection",
  cpu_temperature_c: "CPU temperature (°C)",
  memory_available_bytes: "Memory available (bytes)",
  memory_total_bytes: "Memory total (bytes)",
  storage_free_bytes: "Storage free (bytes)",
  storage_total_bytes: "Storage total (bytes)"
};

let refreshing = false;

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  const status = document.querySelector("#status");
  const section = document.querySelector("#capabilities");
  const healthSection = document.querySelector("#health");
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 5000);
  try {
    const [body, health] = await Promise.all(
      ["/api/v1/capabilities", "/healthz"].map(async (url) => {
        const response = await fetch(url, {
          cache: "no-store", signal: controller.signal
        });
        if (!response.ok) throw new Error("service unavailable");
        return response.json();
      })
    );
    if (!Array.isArray(body.capabilities) || !health || typeof health !== "object") {
      throw new Error("invalid status response");
    }
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
    const healthValues = Object.entries(healthFields).map(([key, label]) => [label, health[key]]);
    healthValues.push(
      ["Platform", health.hardware?.platform?.state],
      ["Board", health.hardware?.platform?.board],
      ["Model", health.hardware?.platform?.model],
      ["Uptime (seconds)", health.hardware?.uptime_seconds]
    );
    if (Array.isArray(health.collection_errors) && health.collection_errors.length) {
      healthValues.push(["Collection diagnostics", health.collection_errors.map(
        (error) => `${error.metric}: ${error.reason}`
      ).join("; ")]);
    }
    healthSection.replaceChildren(...healthValues.flatMap(([label, reading]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const value = document.createElement("dd");
      value.textContent = reading == null ? "Unavailable" : String(reading);
      return [term, value];
    }));
    status.textContent = `Status last received: ${new Date().toLocaleTimeString()}.`;
  } catch {
    controller.abort();
    section.replaceChildren();
    healthSection.replaceChildren();
    status.textContent = "Kenpi status service is unavailable; previous data has been cleared.";
  } finally {
    window.clearTimeout(timeout);
    refreshing = false;
  }
}

refresh();
window.setInterval(refresh, 10000);
