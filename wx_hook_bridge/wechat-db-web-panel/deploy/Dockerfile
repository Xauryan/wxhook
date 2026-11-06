FROM nginx:stable-alpine@sha256:ef8676b33d681f272ba429b27658bdd7e640963279714c96bddf1dc76307f7b6
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY frontend-next/dist/ /usr/share/nginx/html/
COPY frontend-next/LICENSE /usr/share/licenses/shadcn-admin/LICENSE
USER 101:101
EXPOSE 8080
ENTRYPOINT []
CMD ["nginx", "-g", "daemon off;"]
