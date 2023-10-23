FROM stratio/cloud-testing-suite:0.1.0-SNAPSHOT

VOLUME /var/lib/docker

COPY bin/cloud-provisioner.tar.gz /CTS/resources/bin/cloud-provisioner

RUN chmod -R 0700 /CTS

CMD ["bash"]