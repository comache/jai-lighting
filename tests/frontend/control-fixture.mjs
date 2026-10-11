// Synthetic entities only. This preview cannot reach a Home Assistant instance.
export const group = (id) => ({ kind: "group", id });
export const entity = (id) => ({ kind: "entity", id });
const card = (id, target, accent = "lavender", quick = []) => ({
  id,
  target,
  title: "",
  icon: "mdi:lightbulb-outline",
  accent,
  size: "medium",
  quick,
});
export const catalog = {
  groups: {
    main: {
      name: "Main Hall",
      entity_id: "light.synthetic_main",
      parent: null,
      children: ["lounge", "stage"],
      lights: [],
      order: 0,
    },
    lounge: {
      name: "Lounge",
      entity_id: "light.synthetic_lounge",
      parent: "main",
      children: ["accent"],
      lights: ["light.synthetic_glow"],
      order: 0,
    },
    accent: {
      name: "Accent",
      entity_id: "light.synthetic_accent",
      parent: "lounge",
      children: [],
      lights: ["light.synthetic_spot"],
      order: 0,
    },
    stage: {
      name: "Stage",
      entity_id: "light.synthetic_stage",
      parent: "main",
      children: [],
      lights: [],
      order: 1,
    },
    service: {
      name: "Service",
      entity_id: "light.synthetic_service",
      parent: null,
      children: [],
      lights: [],
      order: 1,
    },
    terrace: {
      name: "Terrace",
      entity_id: "light.synthetic_terrace",
      parent: null,
      children: [],
      lights: [],
      order: 2,
    },
  },
  entities: {
    "light.synthetic_glow": {
      name: "Glow",
      domain: "light",
      platform: "demo",
      floor_id: "inside",
    },
    "light.synthetic_spot": {
      name: "Spot",
      domain: "light",
      platform: "demo",
      floor_id: "inside",
    },
    "switch.synthetic_plug": {
      name: "Corner plug",
      domain: "switch",
      platform: "demo",
      floor_id: "inside",
    },
    "sensor.synthetic_temperature": {
      name: "Temperature",
      domain: "sensor",
      platform: "demo",
      floor_id: "inside",
    },
    "media_player.synthetic_music": {
      name: "Spotify example",
      domain: "media_player",
      platform: "spotify",
      floor_id: null,
    },
  },
  collections: {
    upper: {
      name: "Upper accents",
      entities: ["light.synthetic_glow", "light.synthetic_spot"],
    },
  },
  presets: {
    evening: { name: "Evening", entity_id: "scene.synthetic_evening" },
  },
  floors: [
    { id: "inside", name: "Inside" },
    { id: "outside", name: "Outside" },
  ],
  areas: [],
  topology_revision: 5,
};
for (const [id, g] of Object.entries(catalog.groups))
  catalog.entities[g.entity_id] = {
    name: g.name,
    domain: "light",
    platform: "jai_lighting",
    group_id: id,
    floor_id: id === "terrace" ? "outside" : "inside",
  };
export const layout = {
  version: 1,
  title: "J'ai Bistrot",
  sidebar: {
    general: group("main"),
    service: group("service"),
    spotify: entity("media_player.synthetic_music"),
    shortcuts: [entity("switch.synthetic_plug")],
  },
  tabs: [
    {
      id: "inside",
      name: "Interior",
      hidden: false,
      floor_id: "inside",
      cards: [
        card("lounge", group("lounge"), "lavender", [group("accent")]),
        card("stage", group("stage"), "peach"),
        card("glow", entity("light.synthetic_glow"), "mint"),
        card("spot", entity("light.synthetic_spot"), "blue"),
        card("upper", { kind: "collection", id: "upper" }, "rose"),
        card("temp", entity("sensor.synthetic_temperature"), "peach"),
      ],
      moods: [{ kind: "preset", id: "evening" }],
    },
    {
      id: "outside",
      name: "Terasă",
      hidden: false,
      floor_id: "outside",
      cards: [card("terrace", group("terrace"), "mint")],
      moods: [],
    },
    {
      id: "empty",
      name: "Senzori",
      hidden: false,
      floor_id: null,
      cards: [],
      moods: [],
    },
  ],
};
export const states = {};
for (const [id, g] of Object.entries(catalog.groups))
  states[g.entity_id] = {
    entity_id: g.entity_id,
    state: "off",
    attributes: {
      friendly_name: g.name,
      jai_group: true,
      group_id: id,
      master_brightness_pct: 70,
      brightness: 179,
      supported_color_modes: ["brightness"],
      child_group_ids: g.children,
      entity_id: ["light.synthetic_glow", "light.synthetic_spot"],
      unavailable_members: [],
    },
  };
states["light.synthetic_glow"] = {
  entity_id: "light.synthetic_glow",
  state: "on",
  attributes: {
    friendly_name: "Glow",
    brightness: 160,
    supported_color_modes: ["brightness"],
  },
};
states["light.synthetic_spot"] = {
  entity_id: "light.synthetic_spot",
  state: "off",
  attributes: {
    friendly_name: "Spot",
    brightness: 100,
    supported_color_modes: ["onoff"],
  },
};
states["switch.synthetic_plug"] = {
  entity_id: "switch.synthetic_plug",
  state: "off",
  attributes: { friendly_name: "Corner plug" },
};
states["sensor.synthetic_temperature"] = {
  entity_id: "sensor.synthetic_temperature",
  state: "21.7",
  attributes: { friendly_name: "Temperature", unit_of_measurement: "°C" },
};
states["media_player.synthetic_music"] = {
  entity_id: "media_player.synthetic_music",
  state: "paused",
  attributes: {
    friendly_name: "Spotify example",
    media_title: "Evening Sessions",
    media_artist: "Synthetic preview · no audio",
    source: "Example speaker",
    supported_features: 16437,
    volume_level: 0.35,
  },
};
states["scene.synthetic_evening"] = {
  entity_id: "scene.synthetic_evening",
  state: "unknown",
  attributes: { friendly_name: "Evening" },
};
// An inactive scene normally has state "unknown" in HA. Its existence, not prior
// activation, is what makes activation possible.
export function controlFixture() {
  return `<!doctype html><html lang="ro"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>J’ai Control — synthetic preview</title><style>body{margin:0}ha-icon{display:inline-block}</style><body><script type="module">
import '/control.js';
customElements.define('ha-icon',class extends HTMLElement{connectedCallback(){if(!this.shadowRoot){this.attachShadow({mode:'open'}).innerHTML='<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M9 18h6m-6 3h6M8 14a7 7 0 1 1 8 0l-1 3H9z"/></svg>';}}});
window.catalog=${JSON.stringify(catalog)};window.layout=${JSON.stringify(layout)};window.states=${JSON.stringify(states)};window.requests=[];window.events={};window.statusEvents={};window.delay=0;window.failNext=false;
window.saved=JSON.parse(localStorage.getItem('synthetic-layout')||'null');window.revision=window.saved?.revision||0;
window.connection={connected:true,addEventListener:(name,fn)=>window.statusEvents[name]=fn,removeEventListener:(name)=>delete window.statusEvents[name],subscribeEvents:async(fn,name)=>{window.events[name]=fn;return()=>delete window.events[name];}};
window.fakeHass={user:{is_admin:true,id:'synthetic-admin'},states:window.states,connection:window.connection,callWS:async(msg)=>{window.requests.push(structuredClone(msg));if(msg.command==='get')return {catalog:structuredClone(window.catalog),config:structuredClone(window.saved?.config||window.layout),revision:window.revision,saved:!!window.saved};if(msg.command==='save'){if(msg.revision!==window.revision)throw new Error('Layout changed in another session');window.saved={config:structuredClone(msg.config),revision:++window.revision};localStorage.setItem('synthetic-layout',JSON.stringify(window.saved));return window.saved;}if(msg.command==='collection')return {status:'complete'};},callService:async(domain,service,data)=>{window.requests.push({domain,service,data});if(window.delay)await new Promise(r=>setTimeout(r,window.delay));if(window.failNext){window.failNext=false;throw new Error('Synthetic service failure');}const s=window.states[data.entity_id];if(service==='set_master')s.attributes.master_brightness_pct=data.brightness_pct;else if(service==='turn_on'){s.state='on';if(data.brightness_pct!=null)s.attributes.brightness=data.brightness_pct*2.55;}else if(service==='turn_off')s.state='off';else if(service==='media_play')s.state='playing';else if(service==='media_pause')s.state='paused';window.refresh();}};
window.panel=document.createElement('jai-control');document.body.append(window.panel);window.refresh=()=>window.panel.hass={...window.fakeHass,states:{...window.states}};window.refresh();
</script></body></html>`;
}
