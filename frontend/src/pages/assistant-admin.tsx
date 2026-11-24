import type { GetServerSideProps } from "next";

export const getServerSideProps: GetServerSideProps = async () => ({
  redirect: { destination: "/admin/assistant", permanent: false },
});

export default function LegacyAssistantAdminPage() {
  return null;
}
