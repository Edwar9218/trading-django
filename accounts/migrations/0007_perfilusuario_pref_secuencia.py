from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_perfilusuario_pref_sr_fractal"),
    ]

    operations = [
        migrations.AddField(
            model_name="perfilusuario",
            name="pref_secuencia",
            field=models.BooleanField(default=False),
        ),
    ]
