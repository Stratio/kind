FROM stratio/cloud-testing-suite:0.1.0-SNAPSHOT

COPY bin/cloud-provisioner.tar.gz /

CMD ["bash"]