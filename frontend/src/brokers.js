import { brokerEnabled } from "./edition";

const ALL_BROKERS = {
  echoship: "EchoShip",
  tql: "TQL TRAX",
  dsv: "DSV myTMS",
  myunishippers: "MyUnishippers",
  odfl: "Old Dominion",
};

// Only the brokers this edition quotes with (edition.js ENABLED_BROKERS).
export const brokers = Object.fromEntries(Object.entries(ALL_BROKERS).filter(([key]) => brokerEnabled(key)));
