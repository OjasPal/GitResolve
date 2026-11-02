import TaskDetailsPage from "./workspace";

// This protected client workspace waits for authentication and API data.
export const instant = false;

export default function Page() {
  return <TaskDetailsPage />;
}
