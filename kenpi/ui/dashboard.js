// Kenpi touchscreen dashboard. Every screen renders only data returned by
// the running loopback API. Capabilities that are BLOCKED or NOT_VERIFIED
// are shown as explicit "unavailable" states, never as disabled controls
// that imply hidden functionality, and never as fabricated readings.

const CAPABILITY_LABELS = {
  camera_control: "Camera control",
  live_preview: "Live preview",
  media_access: "Camera media",
  kenwood_touchscreen: "Kenwood touchscreen",
  vehicle_power_coordination: "Vehicle coordination",
};

const HEALTH_FIELDS = {
  application_status: "Application",
  collection_status: "Health collection",
  cpu_temperature_c: "CPU temperature (°C)",
  memory_available_bytes: "Memory available (bytes)",
  memory_total_bytes: "Memory total (bytes)",
  storage_free_bytes: "Storage free (bytes)",
  storage_total_bytes: "Storage total (bytes)",
};

// Screens are refreshed every REFRESH_MS. If a successful response has not
// been received within STALE_MS, previously rendered values are flagged as
// stale rather than silently left looking current.
const REFRESH_MS = 10000;
const STALE_MS = REFRESH_MS * 2.5;
const FETCH_TIMEOUT_MS = 5000;

let lastSuccessAt = null;
let refreshing = false;
let activeScreen = "main";

function capabilityByName(capabilities, name) {
  return (
    Array.isArray(capabilities)
      ? capabilities.find((item) => item.name === name)
      : undefined
  ) ?? null;
}

function formatReading(value) {
  return value == null ? "Unavailable" : String(value);
}

function renderDl(container, pairs) {
  container.replaceChildren(
    ...pairs.flatMap(([label, reading]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const value = document.createElement("dd");
      value.textContent = formatReading(reading);
      return [term, value];
    }),
  );
}

function renderCapabilityCard(capability, { title, unavailableHint } = {}) {
  const card = document.createElement("article");
  card.className = "capability-card";
  const heading = document.createElement("h4");
  heading.textContent = title ?? (CAPABILITY_LABELS[capability.name] || capability.name);
  const state = document.createElement("div");
  state.className = `state ${capability.state}`;
  state.textContent = capability.state.replaceAll("_", " ");
  const detail = document.createElement("p");
  detail.textContent = `${capability.detail} · evidence ${capability.evidence}`;
  card.append(heading, state, detail);
  if (capability.state !== "available" && unavailableHint) {
    const hint = document.createElement("p");
    hint.className = "subtle";
    hint.textContent = unavailableHint;
    card.append(hint);
  }
  return card;
}

function renderMissingCapabilityCard(name, title) {
  const card = document.createElement("article");
  card.className = "capability-card";
  const heading = document.createElement("h4");
  heading.textContent = title ?? (CAPABILITY_LABELS[name] || name);
  const state = document.createElement("div");
  state.className = "state blocked";
  state.textContent = "unavailable";
  const detail = document.createElement("p");
  detail.textContent = "Capability was not reported by the running service.";
  card.append(heading, state, detail);
  return card;
}

function renderMainScreen(capabilities, health, serviceStatus) {
  const preview = document.querySelector("#main-preview");
  const liveCapability = capabilityByName(capabilities, "live_preview");
  preview.replaceChildren(
    liveCapability
      ? renderCapabilityCard(liveCapability, {
          title: "Live preview",
          unavailableHint: "No live video stream is attached; nothing is fabricated here.",
        })
      : renderMissingCapabilityCard("live_preview", "Live preview"),
  );

  const camera = capabilityByName(capabilities, "camera_control");
  const media = capabilityByName(capabilities, "media_access");
  const vehicle = capabilityByName(capabilities, "vehicle_power_coordination");
  renderDl(document.querySelector("#main-summary"), [
    ["Recording", camera ? camera.state.replaceAll("_", " ") : "unavailable"],
    ["Camera connection", camera ? camera.state.replaceAll("_", " ") : "unavailable"],
    ["Storage", media ? media.state.replaceAll("_", " ") : "unavailable"],
    ["Vehicle power", vehicle ? vehicle.state.replaceAll("_", " ") : "unavailable"],
    ["Gateway health", health ? health.application_status : null],
  ]);
}

function renderCameraScreen(capabilities) {
  const camera = capabilityByName(capabilities, "camera_control");
  const connection = document.querySelector("#camera-connection");
  connection.replaceChildren(
    camera
      ? renderCapabilityCard(camera, {
          title: "Connection",
          unavailableHint: "No verified A810 Lite transport is connected.",
        })
      : renderMissingCapabilityCard("camera_control", "Connection"),
  );
  const settings = document.querySelector("#camera-settings");
  settings.replaceChildren(
    camera
      ? renderCapabilityCard(camera, {
          title: "Camera settings",
          unavailableHint: "Setting retrieval and modification require verified camera protocol evidence.",
        })
      : renderMissingCapabilityCard("camera_control", "Camera settings"),
  );
  const recording = document.querySelector("#camera-recording");
  recording.replaceChildren(
    camera
      ? renderCapabilityCard(camera, {
          title: "Recording control",
          unavailableHint: "Recording start/stop requires verified camera protocol evidence.",
        })
      : renderMissingCapabilityCard("camera_control", "Recording control"),
  );
}

function formatBytes(value) {
  if (typeof value !== "number") return "Unavailable";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function renderMediaScreen(capabilities, media) {
  const capability = capabilityByName(capabilities, "media_access");
  const status = document.querySelector("#media-status");
  const cards = [
    capability
      ? renderCapabilityCard(capability, {
          title: "Camera storage",
          unavailableHint: "Listing camera files requires verified camera file-access evidence.",
        })
      : renderMissingCapabilityCard("media_access", "Camera storage"),
  ];
  const local = document.createElement("article");
  local.className = "capability-card";
  const heading = document.createElement("h4");
  heading.textContent = "Downloaded copies";
  local.append(heading);
  if (!media || !media.local_enabled) {
    const note = document.createElement("p");
    note.className = "subtle";
    note.textContent = "Local media directory is not configured (media_directory).";
    local.append(note);
  } else if (!Array.isArray(media.files) || media.files.length === 0) {
    const note = document.createElement("p");
    note.className = "subtle";
    note.textContent = "No downloaded media yet.";
    local.append(note);
  } else {
    const list = document.createElement("ul");
    list.replaceChildren(
      ...media.files.map((file) => {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = `/api/v1/media/${encodeURIComponent(file.name)}`;
        link.textContent = file.name;
        link.download = file.name;
        item.append(link, ` · ${file.category.replaceAll("_", " ")} · ${formatBytes(file.size_bytes)}`);
        return item;
      }),
    );
    local.append(list);
  }
  if (media?.storage) {
    const space = document.createElement("p");
    space.className = "subtle";
    space.textContent = `Free: ${formatBytes(media.storage.free_bytes)} of ${formatBytes(media.storage.total_bytes)}`;
    local.append(space);
  }
  cards.push(local);
  status.replaceChildren(...cards);
}

function renderSystemScreen(health, serviceStatus) {
  const healthSection = document.querySelector("#system-health");
  const healthValues = Object.entries(HEALTH_FIELDS).map(
    ([key, label]) => [label, health?.[key]],
  );
  healthValues.push(
    ["Platform", health?.hardware?.platform?.state],
    ["Board", health?.hardware?.platform?.board],
    ["Model", health?.hardware?.platform?.model],
    ["Uptime (seconds)", health?.hardware?.uptime_seconds],
  );
  renderDl(healthSection, healthValues);

  const serviceSection = document.querySelector("#system-service");
  const coordinator = serviceStatus?.coordinator;
  const serviceValues = [
    ["Version", serviceStatus?.version],
    ["Started at", serviceStatus?.started_at],
    [
      "Uptime (seconds)",
      typeof serviceStatus?.uptime_seconds === "number"
        ? serviceStatus.uptime_seconds.toFixed(1)
        : serviceStatus?.uptime_seconds,
    ],
  ];
  if (coordinator && typeof coordinator === "object") {
    serviceValues.push(
      ["Coordinator running", coordinator.is_running],
      [
        "Started services",
        Array.isArray(coordinator.started_services)
          ? coordinator.started_services.join(", ") || "none"
          : coordinator.started_services,
      ],
      [
        "Configured services",
        Array.isArray(coordinator.configured_services)
          ? coordinator.configured_services.join(", ") || "none"
          : coordinator.configured_services,
      ],
    );
  } else if (serviceStatus?.coordinator_error) {
    serviceValues.push(["Coordinator", serviceStatus.coordinator_error]);
  }
  renderDl(serviceSection, serviceValues);

  const networkSection = document.querySelector("#system-network");
  const interfaces = Array.isArray(health?.network?.interfaces)
    ? health.network.interfaces
    : [];
  if (interfaces.length === 0) {
    const none = document.createElement("p");
    none.className = "subtle";
    none.textContent = "No non-loopback network interfaces were reported.";
    networkSection.replaceChildren(none);
  } else {
    const list = document.createElement("ul");
    list.replaceChildren(
      ...interfaces.map((iface) => {
        const item = document.createElement("li");
        const operstate = iface.operstate ?? "unavailable";
        const rx = iface.rx_bytes == null ? "unavailable" : `${iface.rx_bytes} B received`;
        const tx = iface.tx_bytes == null ? "unavailable" : `${iface.tx_bytes} B sent`;
        item.textContent = `${iface.name}: ${operstate} · ${rx} · ${tx}`;
        return item;
      }),
    );
    networkSection.replaceChildren(list);
  }

  const diagnostics = document.querySelector("#system-diagnostics");
  const errors = Array.isArray(health?.collection_errors) ? health.collection_errors : [];
  if (errors.length === 0) {
    const none = document.createElement("p");
    none.className = "subtle";
    none.textContent = "No collection diagnostics reported.";
    diagnostics.replaceChildren(none);
  } else {
    const list = document.createElement("ul");
    list.replaceChildren(
      ...errors.map((error) => {
        const item = document.createElement("li");
        item.textContent = `${error.metric}: ${error.reason}`;
        return item;
      }),
    );
    diagnostics.replaceChildren(list);
  }
}

function renderVehicleScreen(capabilities, vehicle) {
  const capability = capabilityByName(capabilities, "vehicle_power_coordination");
  const status = document.querySelector("#vehicle-status");
  const cards = [
    capability
      ? renderCapabilityCard(capability, {
          title: "Ignition & power coordination",
          unavailableHint:
            "Not physically verified: bench validation of the auxiliary controller is required.",
        })
      : renderMissingCapabilityCard("vehicle_power_coordination", "Ignition & power coordination"),
  ];
  const card = document.createElement("article");
  card.className = "capability-card";
  const heading = document.createElement("h4");
  heading.textContent = "Controller link";
  card.append(heading);
  const list = document.createElement("dl");
  if (!vehicle) {
    renderDl(list, [["Link", null]]);
  } else if (!vehicle.enabled) {
    renderDl(list, [["Link", "disabled (vehicle_uart_enabled is false)"]]);
  } else {
    const link = vehicle.link ?? {};
    renderDl(list, [
      ["Link", link.link_state],
      ["Power state", link.power_state],
      ["Ignition", link.ignition_on == null ? null : link.ignition_on ? "on" : "off"],
      ["Last message age (s)",
        typeof link.last_message_age_seconds === "number"
          ? link.last_message_age_seconds.toFixed(1) : null],
      ["Shutdown requested", link.shutdown_requested],
      ["Shutdown acknowledged", link.shutdown_acknowledged],
      ["Rejected frames", link.rejected_frames],
      ["Replayed frames", link.replayed_frames],
    ]);
  }
  card.append(list);
  cards.push(card);
  status.replaceChildren(...cards);
}

function setScreen(name) {
  const screens = document.querySelectorAll(".screen");
  const tabs = document.querySelectorAll(".tab");
  let matched = false;
  screens.forEach((screen) => {
    const isActive = screen.dataset.screen === name;
    screen.hidden = !isActive;
    if (isActive) matched = true;
  });
  if (!matched) {
    name = "main";
    screens.forEach((screen) => {
      screen.hidden = screen.dataset.screen !== "main";
    });
  }
  tabs.forEach((tab) => {
    const isActive = tab.dataset.target === name;
    tab.classList.toggle("active", isActive);
    if (isActive) {
      tab.setAttribute("aria-current", "page");
    } else {
      tab.removeAttribute("aria-current");
    }
  });
  activeScreen = name;
}

function markStale() {
  const status = document.querySelector("#status");
  if (lastSuccessAt !== null && Date.now() - lastSuccessAt > STALE_MS) {
    status.textContent =
      "Kenpi status is stale; the last successful update was " +
      `${new Date(lastSuccessAt).toLocaleTimeString()}.`;
    status.classList.add("stale");
  }
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  const status = document.querySelector("#status");
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const [capabilityBody, health, serviceStatus] = await Promise.all(
      ["/api/v1/capabilities", "/healthz", "/api/v1/status"].map(async (url) => {
        const response = await fetch(url, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("service unavailable");
        return response.json();
      }),
    );
    if (!Array.isArray(capabilityBody.capabilities) || !health || typeof health !== "object") {
      throw new Error("invalid status response");
    }
    if (!serviceStatus || typeof serviceStatus !== "object") {
      throw new Error("invalid service status response");
    }
    const capabilities = capabilityBody.capabilities;
    const optional = async (url) => {
      try {
        const response = await fetch(url, { cache: "no-store", signal: controller.signal });
        return response.ok ? await response.json() : null;
      } catch {
        return null;
      }
    };
    const [vehicle, media] = await Promise.all([
      optional("/api/v1/vehicle"),
      optional("/api/v1/media"),
    ]);
    renderMainScreen(capabilities, health, serviceStatus);
    renderCameraScreen(capabilities);
    renderMediaScreen(capabilities, media);
    renderSystemScreen(health, serviceStatus);
    renderVehicleScreen(capabilities, vehicle);
    lastSuccessAt = Date.now();
    status.classList.remove("stale");
    status.textContent = `Status last received: ${new Date(lastSuccessAt).toLocaleTimeString()}.`;
  } catch {
    controller.abort();
    markStale();
    if (lastSuccessAt === null) {
      status.textContent = "Kenpi status service is unavailable; no data has been received yet.";
    }
  } finally {
    window.clearTimeout(timeout);
    refreshing = false;
  }
}

function initNavigation() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const target = tab.dataset.target;
      window.location.hash = target;
      setScreen(target);
    });
  });
  window.addEventListener("hashchange", () => {
    setScreen(window.location.hash.replace("#", "") || "main");
  });
  setScreen(window.location.hash.replace("#", "") || "main");
}

initNavigation();
refresh();
window.setInterval(refresh, REFRESH_MS);
window.setInterval(markStale, 1000);
