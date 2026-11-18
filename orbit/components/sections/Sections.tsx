"use client";

import React from "react";
import { useOrbit } from "@/lib/store";
import { HomeSection } from "./HomeSection";
import { AssistantSection } from "./AssistantSection";
import { ProjectsSection } from "./ProjectsSection";
import { TasksSection } from "./TasksSection";
import { CalendarSection } from "./CalendarSection";
import { KnowledgeSection } from "./KnowledgeSection";
import { FilesSection } from "./FilesSection";
import { AutomationsSection } from "./AutomationsSection";
import { IntegrationsSection } from "./IntegrationsSection";
import { ActivitySection } from "./ActivitySection";
import { SettingsSection } from "./SettingsSection";

export function Sections() {
  const { section } = useOrbit();

  switch (section) {
    case "home":
      return <HomeSection />;
    case "assistant":
      return <AssistantSection />;
    case "projects":
      return <ProjectsSection />;
    case "tasks":
      return <TasksSection />;
    case "calendar":
      return <CalendarSection />;
    case "knowledge":
      return <KnowledgeSection />;
    case "files":
      return <FilesSection />;
    case "automations":
      return <AutomationsSection />;
    case "integrations":
      return <IntegrationsSection />;
    case "activity":
      return <ActivitySection />;
    case "settings":
      return <SettingsSection />;
    default:
      return <HomeSection />;
  }
}
