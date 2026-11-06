import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ContactsPage } from "./pages/ContactsPage";
import { IntentionNotificationsPage } from "./pages/IntentionNotificationsPage";
import { DeliveryManagementPage } from "./pages/DeliveryManagementPage";
import { ChatListPage } from "./pages/ChatListPage";
import { ContributionsPage } from "./pages/ContributionsPage";
import { ConversationsPage } from "./pages/ConversationsPage";
import { MediaPage } from "./pages/MediaPage";
import { MpWorkbenchPage } from "./pages/MpWorkbenchPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SearchPage } from "./pages/SearchPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SnsPage } from "./pages/SnsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="overview" element={<OverviewPage />} />
        <Route path="contacts" element={<ContactsPage />} />
        <Route path="intention-notifications" element={<IntentionNotificationsPage />} />
        <Route path="delivery-management" element={<DeliveryManagementPage />} />
        <Route path="media" element={<MediaPage />} />
        <Route path="sns" element={<SnsPage />} />
        <Route path="mp" element={<MpWorkbenchPage />} />
        <Route path="contributions" element={<ContributionsPage />} />
        <Route path="conversations" element={<ConversationsPage />} />
        <Route path="chat-list" element={<ChatListPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  );
}
