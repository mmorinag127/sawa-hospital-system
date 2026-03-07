import type { GetServerSideProps } from "next";

export const getServerSideProps: GetServerSideProps = async () => ({
  redirect: {
    destination: "/facility-master",
    permanent: false,
  },
});

export default function FacilitiesRedirectPage() {
  return null;
}
